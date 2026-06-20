from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
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
        self.assertIn("Non traitées : 0  ", markdown)
        self.assertIn("Statut du traitement : terminé  ", markdown)
        self.assertIn("### Transcription", markdown)

    def test_generate_markdown_marks_partial_status(self) -> None:
        markdown = service.generate_markdown(
            [
                {
                    "url": "https://youtu.be/dQw4w9WgXcQ",
                    "video_id": "dQw4w9WgXcQ",
                    "title": "Pending video",
                    "channel": "Chaîne non disponible",
                    "language_code": None,
                    "language": None,
                    "is_generated": None,
                    "success": None,
                    "transcript_text": "",
                    "error": None,
                    "status": service.STATUS_PENDING,
                }
            ]
        )

        self.assertIn("Non traitées : 1  ", markdown)
        self.assertIn("Statut du traitement : partiel  ", markdown)
        self.assertIn("### Statut", markdown)

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

    @patch("transcript_service.fetch_video_metadata")
    @patch("transcript_service._fetch_transcript_with_retry")
    def test_start_processing_run_saves_progressive_success(
        self,
        mock_fetch_transcript: Mock,
        mock_fetch_metadata: Mock,
    ) -> None:
        mock_fetch_metadata.return_value = {"title": "Real title", "channel": "Real channel"}
        mock_fetch_transcript.return_value = {
            "language_code": "en",
            "language": "English",
            "is_generated": False,
            "transcript_text": "Saved transcript",
        }

        with TemporaryDirectory() as temp_dir:
            run_path = Path(temp_dir) / "current_run.json"
            state = service.start_processing_run(
                "https://youtu.be/dQw4w9WgXcQ",
                run_path=run_path,
                sleep_fn=Mock(),
            )

            loaded_state = service.load_run_state(run_path)

        self.assertTrue(run_path.name, "current_run.json")
        self.assertEqual(state["summary"]["successful"], 1)
        self.assertIsNotNone(loaded_state)
        self.assertEqual(loaded_state["items"][0]["transcript_text"], "Saved transcript")

    @patch("transcript_service.fetch_video_metadata")
    @patch("transcript_service._fetch_transcript_with_retry")
    def test_resume_processing_run_skips_already_successful_videos(
        self,
        mock_fetch_transcript: Mock,
        mock_fetch_metadata: Mock,
    ) -> None:
        mock_fetch_metadata.return_value = {"title": "Second title", "channel": "Second channel"}
        mock_fetch_transcript.return_value = {
            "language_code": "en",
            "language": "English",
            "is_generated": False,
            "transcript_text": "Second transcript",
        }
        first_video = "dQw4w9WgXcQ"
        second_video = "abcDEF_1234"

        with TemporaryDirectory() as temp_dir:
            run_path = Path(temp_dir) / "current_run.json"
            state = service.create_run_state(
                f"https://youtu.be/{first_video}\nhttps://youtu.be/{second_video}"
            )
            state["items"][0].update(
                {
                    "status": service.STATUS_SUCCESS,
                    "success": True,
                    "title": "Cached title",
                    "channel": "Cached channel",
                    "language_code": "en",
                    "language": "English",
                    "is_generated": False,
                    "transcript_text": "Cached transcript",
                    "error": None,
                }
            )
            service.save_run_state(state, run_path)

            resumed_state = service.resume_processing_run(
                run_path=run_path,
                max_videos_per_run=10,
                sleep_fn=Mock(),
            )

        self.assertEqual(resumed_state["summary"]["successful"], 2)
        self.assertEqual(mock_fetch_transcript.call_count, 1)
        mock_fetch_transcript.assert_called_once_with(second_video)

    @patch("transcript_service.fetch_video_metadata")
    @patch("transcript_service._fetch_transcript_with_retry", side_effect=RuntimeError("429 too many requests"))
    def test_throttling_error_stops_without_marking_video_failed(
        self,
        mock_fetch_transcript: Mock,
        mock_fetch_metadata: Mock,
    ) -> None:
        mock_fetch_metadata.return_value = {"title": "Real title", "channel": "Real channel"}

        with TemporaryDirectory() as temp_dir:
            run_path = Path(temp_dir) / "current_run.json"
            state = service.start_processing_run(
                "https://youtu.be/dQw4w9WgXcQ",
                run_path=run_path,
                sleep_fn=Mock(),
            )

        self.assertEqual(state["stopped_reason"], "throttled")
        self.assertEqual(state["message"], service.THROTTLING_MESSAGE)
        self.assertEqual(state["summary"]["unprocessed"], 1)
        self.assertEqual(state["items"][0]["status"], service.STATUS_PENDING)
        mock_fetch_transcript.assert_called_once_with("dQw4w9WgXcQ")

    @patch("transcript_service.fetch_video_metadata")
    @patch("transcript_service._fetch_transcript_with_retry")
    def test_processing_limit_stops_with_partial_message(
        self,
        mock_fetch_transcript: Mock,
        mock_fetch_metadata: Mock,
    ) -> None:
        mock_fetch_metadata.return_value = {"title": "Real title", "channel": "Real channel"}
        mock_fetch_transcript.return_value = {
            "language_code": "en",
            "language": "English",
            "is_generated": False,
            "transcript_text": "Transcript",
        }
        raw_input = "https://youtu.be/dQw4w9WgXcQ\nhttps://youtu.be/abcDEF_1234"

        with TemporaryDirectory() as temp_dir:
            run_path = Path(temp_dir) / "current_run.json"
            state = service.start_processing_run(
                raw_input,
                run_path=run_path,
                max_videos_per_run=1,
                sleep_fn=Mock(),
            )

        self.assertEqual(state["stopped_reason"], "limit_reached")
        self.assertEqual(state["message"], service.PARTIAL_BATCH_MESSAGE)
        self.assertEqual(state["summary"]["successful"], 1)
        self.assertEqual(state["summary"]["unprocessed"], 1)

    def test_large_url_import_is_not_limited_by_max_videos_per_run(self) -> None:
        raw_input = "\n".join(
            f"https://youtu.be/{index:011d}"
            for index in range(service.MAX_VIDEOS_PER_RUN + 20)
        )

        state = service.create_run_state(raw_input)

        self.assertEqual(service.MAX_VIDEOS_PER_RUN, 30)
        self.assertEqual(state["summary"]["total"], 50)
        self.assertEqual(state["summary"]["unprocessed"], 50)


if __name__ == "__main__":
    unittest.main()
