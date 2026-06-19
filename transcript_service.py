from __future__ import annotations

from datetime import datetime
import html
import random
import re
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests
from youtube_transcript_api import YouTubeTranscriptApi


OEMBED_ENDPOINT = "https://www.youtube.com/oembed"
REQUEST_TIMEOUT_SECONDS = 10
DELAY_BETWEEN_VIDEOS_SECONDS = 1
RETRY_BACKOFF_RANGE_SECONDS = (2, 3)
VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")
YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
}


def extract_video_id(url: str) -> str | None:
    candidate = (url or "").strip()
    if not candidate:
        return None

    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", candidate):
        candidate = f"https://{candidate}"

    parsed_url = urlparse(candidate)
    host = parsed_url.netloc.lower().split(":")[0]
    path_parts = [part for part in parsed_url.path.split("/") if part]

    video_id: str | None = None
    if host == "youtu.be" and path_parts:
        video_id = path_parts[0]
    elif host in YOUTUBE_HOSTS:
        if parsed_url.path == "/watch":
            video_id = parse_qs(parsed_url.query).get("v", [None])[0]
        elif len(path_parts) >= 2 and path_parts[0] == "shorts":
            video_id = path_parts[1]

    if video_id and VIDEO_ID_PATTERN.fullmatch(video_id):
        return video_id
    return None


def parse_urls(raw_input: str) -> list[dict[str, Any]]:
    parsed_urls: list[dict[str, Any]] = []

    for raw_line in raw_input.splitlines():
        url = raw_line.strip()
        if not url:
            continue

        video_id = extract_video_id(url)
        parsed_urls.append(
            {
                "url": url,
                "video_id": video_id,
                "error": None if video_id else "URL YouTube invalide ou ID vidéo introuvable.",
            }
        )

    return parsed_urls


def deduplicate_videos(parsed_urls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen_video_ids: set[str] = set()
    unique_urls: list[dict[str, Any]] = []

    for parsed_url in parsed_urls:
        video_id = parsed_url.get("video_id")
        if not video_id:
            unique_urls.append(parsed_url)
            continue

        if video_id in seen_video_ids:
            continue

        seen_video_ids.add(video_id)
        unique_urls.append(parsed_url)

    return unique_urls


def fetch_video_metadata(url: str, video_id: str) -> dict[str, str]:
    fallback = {
        "title": f"Vidéo {video_id}",
        "channel": "Chaîne non disponible",
    }

    canonical_url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        response = requests.get(
            OEMBED_ENDPOINT,
            params={"url": canonical_url, "format": "json"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError):
        return fallback

    return {
        "title": str(data.get("title") or fallback["title"]).strip(),
        "channel": str(data.get("author_name") or fallback["channel"]).strip(),
    }


def select_primary_transcript(transcript_list: Any) -> Any:
    for transcript in transcript_list:
        return transcript

    raise ValueError("Aucune transcription disponible pour cette vidéo.")


def fetch_transcript(video_id: str) -> dict[str, Any]:
    ytt_api = YouTubeTranscriptApi()
    transcript_list = ytt_api.list(video_id)
    transcript = select_primary_transcript(transcript_list)

    language_code = transcript.language_code
    language = transcript.language
    is_generated = transcript.is_generated

    fetched_transcript = transcript.fetch()
    transcript_text = clean_transcript(fetched_transcript)

    if not transcript_text:
        raise ValueError("La transcription récupérée est vide.")

    return {
        "language_code": language_code,
        "language": language,
        "is_generated": is_generated,
        "transcript_text": transcript_text,
    }


def clean_transcript(fetched_transcript: Any) -> str:
    texts = [snippet.text for snippet in fetched_transcript]
    return clean_text_segments(texts)


def clean_text_segments(segments: list[str]) -> str:
    cleaned_segments: list[str] = []

    for segment in segments:
        text = html.unescape(str(segment or ""))
        text = text.replace("\n", " ")
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            cleaned_segments.append(text)

    return " ".join(cleaned_segments).strip()


def process_urls(raw_input: str) -> list[dict[str, Any]]:
    parsed_urls = deduplicate_videos(parse_urls(raw_input))
    results: list[dict[str, Any]] = []

    for index, parsed_url in enumerate(parsed_urls):
        video_id = parsed_url.get("video_id")
        original_url = parsed_url["url"]

        if not video_id:
            results.append(_build_error_result(original_url, None, parsed_url["error"]))
            continue

        metadata = fetch_video_metadata(original_url, video_id)

        try:
            transcript_data = _fetch_transcript_with_retry(video_id)
            results.append(
                {
                    "url": original_url,
                    "video_id": video_id,
                    "title": metadata["title"],
                    "channel": metadata["channel"],
                    "language_code": transcript_data["language_code"],
                    "language": transcript_data["language"],
                    "is_generated": transcript_data["is_generated"],
                    "success": True,
                    "transcript_text": transcript_data["transcript_text"],
                    "error": None,
                }
            )
        except Exception as exc:
            results.append(
                _build_error_result(
                    original_url,
                    video_id,
                    _friendly_error_message(exc),
                    metadata["title"],
                    metadata["channel"],
                )
            )

        if _has_later_video(parsed_urls, index):
            time.sleep(DELAY_BETWEEN_VIDEOS_SECONDS)

    return results


def generate_markdown(results: list[dict[str, Any]]) -> str:
    summary = build_summary(results)
    generated_at = datetime.now().strftime("%d/%m/%Y %H:%M")

    lines = [
        "# Transcriptions YouTube",
        "",
        f"Généré le : {generated_at}",
        "",
        f"Nombre total de vidéos : {summary['total']}  ",
        f"Transcriptions réussies : {summary['successful']}  ",
        f"Échecs : {summary['failed']}  ",
        "",
        "---",
        "",
    ]

    for index, result in enumerate(results, start=1):
        title = _escape_markdown(
            _single_line(result.get("title"), f"Vidéo {result.get('video_id') or index}")
        )
        channel = _escape_markdown(
            _single_line(result.get("channel"), "Chaîne non disponible")
        )
        url = _single_line(result.get("url"), "URL non disponible")
        video_id = _single_line(result.get("video_id"), "ID non disponible")

        lines.extend(
            [
                f"## {index}. {title}",
                "",
                f"Chaîne : {channel}  ",
                f"URL : {url}  ",
                f"ID vidéo : {video_id}  ",
            ]
        )

        if result.get("success"):
            lines.extend(
                [
                    f"Langue : {_single_line(result.get('language'), 'Non disponible')}  ",
                    f"Code langue : {_single_line(result.get('language_code'), 'Non disponible')}  ",
                    (
                        "Transcription générée automatiquement : "
                        f"{_format_generated_flag(result.get('is_generated'))}  "
                    ),
                    "",
                    "### Transcription",
                    "",
                    result.get("transcript_text", "").strip() or "Transcription vide.",
                    "",
                    "---",
                    "",
                ]
            )
        else:
            lines.extend(
                [
                    "",
                    "### Erreur",
                    "",
                    _single_line(result.get("error"), "Erreur inconnue."),
                    "",
                    "---",
                    "",
                ]
            )

    return "\n".join(lines).rstrip() + "\n"


def build_summary(results: list[dict[str, Any]]) -> dict[str, int]:
    successful = sum(1 for result in results if result.get("success"))
    total = len(results)
    return {
        "total": total,
        "successful": successful,
        "failed": total - successful,
    }


def _fetch_transcript_with_retry(video_id: str) -> dict[str, Any]:
    try:
        return fetch_transcript(video_id)
    except Exception as exc:
        if not _is_temporary_error(exc):
            raise

        time.sleep(random.uniform(*RETRY_BACKOFF_RANGE_SECONDS))
        return fetch_transcript(video_id)


def _is_temporary_error(exc: Exception) -> bool:
    if isinstance(exc, requests.RequestException):
        return True

    class_name = exc.__class__.__name__.lower()
    permanent_error_classes = (
        "notranscriptfound",
        "transcriptsdisabled",
        "videounavailable",
    )
    if any(error_class in class_name for error_class in permanent_error_classes):
        return False

    message = str(exc).lower()
    temporary_keywords = (
        "429",
        "too many requests",
        "rate limit",
        "tempor",
        "timeout",
        "timed out",
        "network",
        "connection",
        "requestblocked",
        "request blocked",
        "ipblocked",
        "ip blocked",
        "could not retrieve",
        "server error",
    )
    return any(keyword in class_name or keyword in message for keyword in temporary_keywords)


def _friendly_error_message(exc: Exception) -> str:
    class_name = exc.__class__.__name__.lower()
    message = str(exc).strip()

    if "transcriptsdisabled" in class_name:
        return "Transcription désactivée pour cette vidéo."
    if "notranscriptfound" in class_name:
        return "Aucune transcription disponible pour cette vidéo."
    if "videounavailable" in class_name:
        return "Vidéo privée, supprimée ou indisponible."
    if "429" in message or "too many requests" in message.lower() or "rate limit" in message.lower():
        return "YouTube limite temporairement les requêtes. Réessayez plus tard."
    if "timeout" in message.lower() or "timed out" in message.lower():
        return "Timeout pendant la récupération de la transcription."
    if "requestblocked" in class_name or "ipblocked" in class_name:
        return "Blocage temporaire de la requête par YouTube. Réessayez plus tard."

    return f"Impossible de récupérer la transcription : {message or exc.__class__.__name__}"


def _build_error_result(
    original_url: str,
    video_id: str | None,
    error: str,
    title: str | None = None,
    channel: str | None = None,
) -> dict[str, Any]:
    return {
        "url": original_url,
        "video_id": video_id,
        "title": title or (f"Vidéo {video_id}" if video_id else "Vidéo en erreur"),
        "channel": channel or "Chaîne non disponible",
        "language_code": None,
        "language": None,
        "is_generated": None,
        "success": False,
        "transcript_text": "",
        "error": error,
    }


def _has_later_video(parsed_urls: list[dict[str, Any]], current_index: int) -> bool:
    return any(item.get("video_id") for item in parsed_urls[current_index + 1 :])


def _single_line(value: Any, fallback: str) -> str:
    text = str(value or "").replace("\n", " ").strip()
    text = re.sub(r"\s+", " ", text)
    return text or fallback


def _escape_markdown(value: str) -> str:
    return re.sub(r"([\\`*_{}\[\]()#+\-.!|>])", r"\\\1", value)


def _format_generated_flag(value: Any) -> str:
    if value is True:
        return "oui"
    if value is False:
        return "non"
    return "Non disponible"
