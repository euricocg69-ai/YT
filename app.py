from __future__ import annotations

from datetime import date

import streamlit as st

from transcript_service import build_summary, generate_markdown, process_urls


WARNING_OVER_25_URLS = (
    "Attention : vous avez saisi plus de 25 URL. Le traitement peut être plus "
    "lent et YouTube peut limiter temporairement certaines requêtes. "
    "L'application va traiter les vidéos une par une avec un délai de sécurité."
)


def initialize_session_state() -> None:
    defaults = {
        "results": [],
        "markdown_content": "",
        "summary": {"total": 0, "successful": 0, "failed": 0},
        "last_input_urls": "",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def count_entered_urls(raw_input: str) -> int:
    return sum(1 for line in raw_input.splitlines() if line.strip())


def render_results() -> None:
    results = st.session_state["results"]
    markdown_content = st.session_state["markdown_content"]

    if not results:
        return

    summary = st.session_state["summary"]
    st.divider()

    col_total, col_success, col_failed = st.columns(3)
    col_total.metric("Vidéos traitées", summary["total"])
    col_success.metric("Succès", summary["successful"])
    col_failed.metric("Échecs", summary["failed"])

    st.subheader("Résultats")
    for index, result in enumerate(results, start=1):
        title = result.get("title") or f"Vidéo {result.get('video_id') or index}"
        video_id = result.get("video_id") or "ID indisponible"
        channel = result.get("channel") or "Chaîne non disponible"

        if result.get("success"):
            st.success(f"{index}. {title}")
            st.write(
                f"Chaîne : {channel}  \n"
                f"ID vidéo : {video_id}  \n"
                f"Langue : {result.get('language') or 'Non disponible'}"
            )
        else:
            st.error(f"{index}. {title}")
            st.write(
                f"Chaîne : {channel}  \n"
                f"ID vidéo : {video_id}  \n"
                f"Erreur : {result.get('error') or 'Erreur inconnue'}"
            )

    if markdown_content:
        file_name = f"youtube_transcriptions_{date.today().isoformat()}.md"
        st.download_button(
            "Télécharger le Markdown",
            data=markdown_content,
            file_name=file_name,
            mime="text/markdown",
            key="download_markdown",
        )


def main() -> None:
    st.set_page_config(
        page_title="YouTube Transcript Markdown Exporter",
        page_icon="YT",
        layout="centered",
    )
    initialize_session_state()

    st.title("YouTube Transcript Markdown Exporter")
    st.write(
        "Collez plusieurs URL YouTube, récupérez les transcriptions disponibles "
        "et exportez un fichier Markdown unique."
    )

    raw_input = st.text_area(
        "URL YouTube",
        placeholder=(
            "https://www.youtube.com/watch?v=VIDEO_ID\n"
            "https://youtu.be/VIDEO_ID\n"
            "https://www.youtube.com/shorts/VIDEO_ID"
        ),
        height=220,
    )

    entered_url_count = count_entered_urls(raw_input)
    if entered_url_count > 25:
        st.warning(WARNING_OVER_25_URLS)

    if st.button("Générer le Markdown", type="primary"):
        if entered_url_count == 0:
            st.error("Collez au moins une URL YouTube avant de générer le Markdown.")
        else:
            with st.spinner("Récupération des transcriptions en cours..."):
                results = process_urls(raw_input)
                markdown_content = generate_markdown(results)
                summary = build_summary(results)

            st.session_state["results"] = results
            st.session_state["markdown_content"] = markdown_content
            st.session_state["summary"] = summary
            st.session_state["last_input_urls"] = raw_input
            st.success("Markdown généré.")

    if (
        st.session_state["results"]
        and raw_input != st.session_state["last_input_urls"]
    ):
        st.info("L'entrée a changé depuis la dernière génération.")

    render_results()


if __name__ == "__main__":
    main()
