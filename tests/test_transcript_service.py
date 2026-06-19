from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

import transcript_service as service


class TranscriptServiceTest(unittest.TestCase):
    def test_extract_video_id_accepts_supported_formats(self) -> None:
        expected_video_id = "dQw4w9WgXcQ"

        self.assertEqual(
            service.extract_video_id(f"https://www.youtube.com/watch?v={expected_video_id}"),
            expected_video_id,
        )
        self.assertEqual(
            service.extract_video_id(f"https://youtube.com/watch?v={expected_video_id}"),
            expected_video_id,
        )
        self.assertEqual(
            service.extract_video_id(f"https://youtu.be/{expected_video_id}"),
            expected_video_id,
        )
        self.assertEqual(
            service.extract_video_id(f"https://www.youtube.com/shorts/{expected_video_id}"),
            expected_video_id,
        )
        self.assertEqual(
            service.extract_video_id(f"https://youtube.com/shorts/{expected_video_id}"),
            expected_video_id,
        )

    def test_extract_video_id_rejects_invalid_urls(self) -> None:
        self.assertIsNone(service.extract_video_id(""))
        self.assertIsNone(service.extract_video_id("not-a-youtube-url"))
        self.assertIsNone(service.extract_video_id("https://www.youtube.com/watch?v=short"))

    def test_parse_urls_ignores_empty_lines_and_marks_invalid_urls(self) -> None:
        parsed_urls = service.parse_urls(
            "\n"
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ\n"
            "   \n"
            "invalid\n"
        )

        self.assertEqual(len(parsed_urls), 2)
        self.assertEqual(parsed_urls[0]["video_id"], "dQw4w9WgXcQ")
        self.assertIsNone(parsed_urls[0]["error"])
        self.assertIsNone(parsed_urls[1]["video_id"])
        self.assertEqual(parsed_urls[1]["error"], "URL YouTube invalide ou ID vidéo introuvable.")

    def test_deduplicate_videos_keeps_first_valid_occurrence(self) -> None:
        parsed_urls = [
            {"url": "https://youtu.be/dQw4w9WgXcQ", "video_id": "dQw4w9WgXcQ"},
            {
                "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "video_id": "dQw4w9WgXcQ",
            },
            {"url": "invalid", "video_id": None},
            {"url": "https://youtu.be/abcDEF_1234", "video_id": "abcDEF_1234"},
        ]

        unique_urls = service.deduplicate_videos(parsed_urls)

        self.assertEqual([item["url"] for item in unique_urls], [
            "https://youtu.be/dQw4w9WgXcQ",
            "invalid",
            "https://youtu.be/abcDEF_1234",
        ])

    def test_clean_text_segments_unescapes_and_normalizes_text(self) -> None:
        self.assertEqual(
            service.clean_text_segments([" Hello\nworld ", "Tom &amp; Jerry", "", "again"]),
            "Hello world Tom & Jerry again",
        )

    def test_generate_markdown_escapes_title_and_channel(self) -> None:
        markdown = service.generate_markdown(
            [
                {
                    "url": "https://youtu.be/dQw4w9WgXcQ",
                    "video_id": "dQw4w9WgXcQ",
                    "title": "A *bold* [title]",
                    "channel": "Channel #1",
                    "language_code": "en",
                    "language": "English",
                    "is_generated": False,
                    "success": True,
                    "transcript_text": "Transcript text.",
                    "error": None,
                }
            ]
        )

        self.assertIn("## 1. A \\*bold\\* \\[title\\]", markdown)
        self.assertIn("Chaîne : Channel \\#1  ", markdown)
        self.assertIn("### Transcription", markdown)

    @patch("transcript_service.requests.get")
    def test_fetch_video_metadata_oembed_success(self, mock_get: Mock) -> None:
        response = Mock()
        response.json.return_value = {"title": "Real title", "author_name": "Real channel"}
        response.raise_for_status.return_value = None
        mock_get.return_value = response

        metadata = service.fetch_video_metadata("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ")

        self.assertEqual(metadata, {"title": "Real title", "channel": "Real channel"})
        mock_get.assert_called_once()

    @patch("transcript_service.requests.get", side_effect=requests.Timeout)
    def test_fetch_video_metadata_oembed_failure_uses_fallback(self, mock_get: Mock) -> None:
        metadata = service.fetch_video_metadata("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ")

        self.assertEqual(
            metadata,
            {
                "title": "Vidéo dQw4w9WgXcQ",
                "channel": "Chaîne non disponible",
            },
        )
        mock_get.assert_called_once()

    @patch("transcript_service.YouTubeTranscriptApi")
    def test_fetch_transcript_success(self, mock_api_class: Mock) -> None:
        transcript = Mock()
        transcript.language_code = "en"
        transcript.language = "English"
        transcript.is_generated = True
        transcript.fetch.return_value = [
            SimpleNamespace(text="Hello"),
            SimpleNamespace(text="world"),
        ]
        mock_api_class.return_value.list.return_value = [transcript]

        result = service.fetch_transcript("dQw4w9WgXcQ")

        self.assertEqual(result["language_code"], "en")
        self.assertEqual(result["language"], "English")
        self.assertIs(result["is_generated"], True)
        self.assertEqual(result["transcript_text"], "Hello world")
        mock_api_class.return_value.list.assert_called_once_with("dQw4w9WgXcQ")

    @patch("transcript_service.fetch_video_metadata")
    @patch("transcript_service._fetch_transcript_with_retry", side_effect=RuntimeError("Boom"))
    def test_process_urls_transcription_error_returns_video_error(
        self,
        mock_fetch_transcript: Mock,
        mock_fetch_metadata: Mock,
    ) -> None:
        mock_fetch_metadata.return_value = {
            "title": "Real title",
            "channel": "Real channel",
        }

        results = service.process_urls("https://youtu.be/dQw4w9WgXcQ")

        self.assertEqual(len(results), 1)
        self.assertFalse(results[0]["success"])
        self.assertEqual(results[0]["title"], "Real title")
        self.assertEqual(results[0]["channel"], "Real channel")
        self.assertIn("Impossible de récupérer la transcription", results[0]["error"])
        mock_fetch_transcript.assert_called_once_with("dQw4w9WgXcQ")


if __name__ == "__main__":
    unittest.main()
