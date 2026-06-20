from __future__ import annotations

from datetime import datetime
import html
import json
from pathlib import Path
import random
import re
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests
from youtube_transcript_api import YouTubeTranscriptApi


OEMBED_ENDPOINT = "https://www.youtube.com/oembed"
REQUEST_TIMEOUT_SECONDS = 10
MAX_VIDEOS_PER_RUN = 15
TRANSCRIPT_DELAY_SECONDS = 5
BATCH_COOLDOWN_EVERY = 10
BATCH_COOLDOWN_SECONDS = 30
DELAY_BETWEEN_VIDEOS_SECONDS = TRANSCRIPT_DELAY_SECONDS
RETRY_BACKOFF_RANGE_SECONDS = (2, 3)
CURRENT_RUN_PATH = Path("output/current_run.json")
THROTTLING_MESSAGE = (
    "YouTube limite temporairement les requêtes. Les résultats déjà récupérés "
    "sont sauvegardés. Attendez quelques minutes puis reprenez le traitement."
)
PARTIAL_BATCH_MESSAGE = (
    "Lot partiel terminé. Vous pouvez reprendre le traitement après une pause."
)
VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")
YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
}
STATUS_PENDING = "pending"
STATUS_SUCCESS = "success"
STATUS_ERROR = "error"


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


def load_run_state(run_path: Path = CURRENT_RUN_PATH) -> dict[str, Any] | None:
    if not run_path.exists():
        return None

    try:
        with run_path.open("r", encoding="utf-8") as file:
            state = json.load(file)
    except (OSError, json.JSONDecodeError):
        return None

    if not isinstance(state, dict) or not isinstance(state.get("items"), list):
        return None
    return state


def save_run_state(state: dict[str, Any], run_path: Path = CURRENT_RUN_PATH) -> None:
    state["updated_at"] = datetime.now().isoformat(timespec="seconds")
    state["summary"] = build_summary(state.get("items", []))
    state["is_complete"] = state["summary"]["unprocessed"] == 0
    run_path.parent.mkdir(parents=True, exist_ok=True)
    with run_path.open("w", encoding="utf-8") as file:
        json.dump(state, file, ensure_ascii=False, indent=2)


def create_run_state(
    raw_input: str,
    previous_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    parsed_urls = deduplicate_videos(parse_urls(raw_input))
    success_cache = _build_success_cache(previous_state)
    items: list[dict[str, Any]] = []

    for parsed_url in parsed_urls:
        original_url = parsed_url["url"]
        video_id = parsed_url.get("video_id")

        if not video_id:
            error_result = _build_error_result(original_url, None, parsed_url["error"])
            error_result["status"] = STATUS_ERROR
            items.append(error_result)
            continue

        cached_result = success_cache.get(video_id)
        if cached_result:
            item = dict(cached_result)
            item["url"] = original_url
            item["status"] = STATUS_SUCCESS
            item["success"] = True
            item["error"] = None
            items.append(item)
            continue

        items.append(_build_pending_result(original_url, video_id))

    now = datetime.now().isoformat(timespec="seconds")
    state = {
        "created_at": now,
        "updated_at": now,
        "raw_input": raw_input,
        "items": items,
        "message": "",
        "stopped_reason": None,
        "is_complete": False,
        "summary": build_summary(items),
    }
    state["is_complete"] = state["summary"]["unprocessed"] == 0
    return state


def start_processing_run(
    raw_input: str,
    run_path: Path = CURRENT_RUN_PATH,
    max_videos_per_run: int = MAX_VIDEOS_PER_RUN,
    sleep_fn: Any = time.sleep,
) -> dict[str, Any]:
    previous_state = load_run_state(run_path)
    state = create_run_state(raw_input, previous_state)
    save_run_state(state, run_path)
    return process_run_state(state, run_path, max_videos_per_run, sleep_fn)


def resume_processing_run(
    run_path: Path = CURRENT_RUN_PATH,
    max_videos_per_run: int = MAX_VIDEOS_PER_RUN,
    sleep_fn: Any = time.sleep,
) -> dict[str, Any]:
    state = load_run_state(run_path)
    if not state:
        state = create_run_state("")
        state["message"] = "Aucun traitement sauvegardé à reprendre."
        save_run_state(state, run_path)
        return state

    return process_run_state(state, run_path, max_videos_per_run, sleep_fn)


def process_run_state(
    state: dict[str, Any],
    run_path: Path = CURRENT_RUN_PATH,
    max_videos_per_run: int = MAX_VIDEOS_PER_RUN,
    sleep_fn: Any = time.sleep,
) -> dict[str, Any]:
    processed_this_run = 0
    state["message"] = ""
    state["stopped_reason"] = None

    for index, item in enumerate(state.get("items", [])):
        if processed_this_run >= max_videos_per_run:
            state["message"] = PARTIAL_BATCH_MESSAGE
            state["stopped_reason"] = "limit_reached"
            break

        if item.get("status") != STATUS_PENDING:
            continue

        video_id = item.get("video_id")
        if not video_id:
            item.update(_build_error_result(item.get("url", ""), None, "ID vidéo introuvable."))
            item["status"] = STATUS_ERROR
            save_run_state(state, run_path)
            continue

        metadata = fetch_video_metadata(item["url"], video_id)
        item["title"] = metadata["title"]
        item["channel"] = metadata["channel"]

        try:
            transcript_data = _fetch_transcript_with_retry(video_id)
        except Exception as exc:
            if _is_throttling_error(exc):
                item["last_error"] = THROTTLING_MESSAGE
                state["message"] = THROTTLING_MESSAGE
                state["stopped_reason"] = "throttled"
                save_run_state(state, run_path)
                break

            item.update(
                _build_error_result(
                    item["url"],
                    video_id,
                    _friendly_error_message(exc),
                    metadata["title"],
                    metadata["channel"],
                )
            )
            item["status"] = STATUS_ERROR
            save_run_state(state, run_path)
            processed_this_run += 1
        else:
            item.update(
                {
                    "url": item["url"],
                    "video_id": video_id,
                    "title": metadata["title"],
                    "channel": metadata["channel"],
                    "language_code": transcript_data["language_code"],
                    "language": transcript_data["language"],
                    "is_generated": transcript_data["is_generated"],
                    "success": True,
                    "transcript_text": transcript_data["transcript_text"],
                    "error": None,
                    "last_error": None,
                    "status": STATUS_SUCCESS,
                }
            )
            save_run_state(state, run_path)
            processed_this_run += 1

        if (
            state.get("stopped_reason") is None
            and processed_this_run < max_videos_per_run
            and _has_later_pending_video(state.get("items", []), index)
        ):
            if processed_this_run % BATCH_COOLDOWN_EVERY == 0:
                sleep_fn(BATCH_COOLDOWN_SECONDS)
            else:
                sleep_fn(TRANSCRIPT_DELAY_SECONDS)

    if state.get("stopped_reason") is None:
        summary = build_summary(state.get("items", []))
        if summary["unprocessed"] == 0:
            state["message"] = "Traitement terminé."
            state["stopped_reason"] = "complete"
        elif processed_this_run >= max_videos_per_run:
            state["message"] = PARTIAL_BATCH_MESSAGE
            state["stopped_reason"] = "limit_reached"

    save_run_state(state, run_path)
    return state


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
                _build_success_result(original_url, video_id, metadata, transcript_data)
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
        f"Non traitées : {summary['unprocessed']}  ",
        f"Statut du traitement : {summary['status_label']}  ",
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

        status = _item_status(result)
        if status == STATUS_SUCCESS:
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
        elif status == STATUS_PENDING:
            lines.extend(
                [
                    "",
                    "### Statut",
                    "",
                    result.get("last_error") or "Non traitée pour le moment.",
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


def build_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    successful = sum(1 for result in results if _item_status(result) == STATUS_SUCCESS)
    failed = sum(1 for result in results if _item_status(result) == STATUS_ERROR)
    unprocessed = sum(1 for result in results if _item_status(result) == STATUS_PENDING)
    total = len(results)
    is_complete = unprocessed == 0
    return {
        "total": total,
        "successful": successful,
        "failed": failed,
        "unprocessed": unprocessed,
        "is_complete": is_complete,
        "status_label": "terminé" if is_complete else "partiel",
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


def _is_throttling_error(exc: Exception) -> bool:
    class_name = exc.__class__.__name__.lower()
    message = str(exc).lower()
    throttling_keywords = (
        "429",
        "too many requests",
        "rate limit",
        "requestblocked",
        "request blocked",
        "ipblocked",
        "ip blocked",
    )
    return any(keyword in class_name or keyword in message for keyword in throttling_keywords)


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


def _build_success_result(
    original_url: str,
    video_id: str,
    metadata: dict[str, str],
    transcript_data: dict[str, Any],
) -> dict[str, Any]:
    return {
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
        "last_error": None,
        "status": STATUS_SUCCESS,
    }


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
        "last_error": None,
        "status": STATUS_ERROR,
    }


def _build_pending_result(original_url: str, video_id: str) -> dict[str, Any]:
    return {
        "url": original_url,
        "video_id": video_id,
        "title": f"Vidéo {video_id}",
        "channel": "Chaîne non disponible",
        "language_code": None,
        "language": None,
        "is_generated": None,
        "success": None,
        "transcript_text": "",
        "error": None,
        "last_error": None,
        "status": STATUS_PENDING,
    }


def _build_success_cache(state: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not state:
        return {}

    cache: dict[str, dict[str, Any]] = {}
    for item in state.get("items", []):
        video_id = item.get("video_id")
        if video_id and _item_status(item) == STATUS_SUCCESS and item.get("transcript_text"):
            cache[video_id] = item
    return cache


def _has_later_video(parsed_urls: list[dict[str, Any]], current_index: int) -> bool:
    return any(item.get("video_id") for item in parsed_urls[current_index + 1 :])


def _has_later_pending_video(items: list[dict[str, Any]], current_index: int) -> bool:
    return any(_item_status(item) == STATUS_PENDING for item in items[current_index + 1 :])


def _item_status(result: dict[str, Any]) -> str:
    status = result.get("status")
    if status in {STATUS_PENDING, STATUS_SUCCESS, STATUS_ERROR}:
        return status
    if result.get("success") is True:
        return STATUS_SUCCESS
    if result.get("success") is False:
        return STATUS_ERROR
    return STATUS_PENDING


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
