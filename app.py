from __future__ import annotations

from datetime import date

import streamlit as st

from transcript_service import (
    BATCH_COOLDOWN_SECONDS,
    CURRENT_RUN_PATH,
    MAX_VIDEOS_PER_RUN,
    THROTTLING_MESSAGE,
    TRANSCRIPT_DELAY_SECONDS,
    build_summary,
    generate_markdown,
    load_run_state,
    resume_processing_run,
    start_processing_run,
)


LARGE_BATCH_MESSAGE = (
    "Vous pouvez coller beaucoup d'URL. L'application les placera en file "
    "d'attente et les traitera par lots pour limiter les blocages YouTube."
)


def initialize_session_state() -> None:
    saved_state = load_run_state()
    saved_results = saved_state.get("items", []) if saved_state else []
    defaults = {
        "run_state": saved_state,
        "results": saved_results,
        "markdown_content": generate_markdown(saved_results) if saved_results else "",
        "summary": build_summary(saved_results),
        "last_input_urls": saved_state.get("raw_input", "") if saved_state else "",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def count_entered_urls(raw_input: str) -> int:
    return sum(1 for line in raw_input.splitlines() if line.strip())


def update_session_from_run_state(run_state: dict) -> None:
    results = run_state.get("items", [])
    st.session_state["run_state"] = run_state
    st.session_state["results"] = results
    st.session_state["markdown_content"] = generate_markdown(results)
    st.session_state["summary"] = build_summary(results)
    st.session_state["last_input_urls"] = run_state.get("raw_input", "")


def render_status_message() -> None:
    run_state = st.session_state.get("run_state")
    if not run_state:
        return

    message = run_state.get("message")
    stopped_reason = run_state.get("stopped_reason")
    if not message:
        return

    if stopped_reason == "throttled":
        st.warning(message)
    elif stopped_reason == "complete":
        st.success(message)
    else:
        st.info(message)


def render_results() -> None:
    results = st.session_state["results"]
    markdown_content = st.session_state["markdown_content"]

    if not results:
        return

    summary = st.session_state["summary"]
    st.divider()

    col_total, col_success, col_failed, col_pending = st.columns(4)
    col_total.metric("Total URL", summary["total"])
    col_success.metric("Réussies", summary["successful"])
    col_failed.metric("Échouées", summary["failed"])
    col_pending.metric("Non traitées", summary["unprocessed"])

    st.subheader("Résultats")
    for index, result in enumerate(results, start=1):
        title = result.get("title") or f"Vidéo {result.get('video_id') or index}"
        video_id = result.get("video_id") or "ID indisponible"
        channel = result.get("channel") or "Chaîne non disponible"
        status = result.get("status")

        if result.get("success") is True or status == "success":
            st.success(f"{index}. {title}")
            st.write(
                f"Chaîne : {channel}  \n"
                f"ID vidéo : {video_id}  \n"
                f"Langue : {result.get('language') or 'Non disponible'}"
            )
        elif result.get("success") is False or status == "error":
            st.error(f"{index}. {title}")
            st.write(
                f"Chaîne : {channel}  \n"
                f"ID vidéo : {video_id}  \n"
                f"Erreur : {result.get('error') or 'Erreur inconnue'}"
            )
        else:
            st.info(f"{index}. {title}")
            st.write(
                f"Chaîne : {channel}  \n"
                f"ID vidéo : {video_id}  \n"
                f"Statut : non traitée"
            )

    if markdown_content:
        file_name = f"youtube_transcriptions_{date.today().isoformat()}.md"
        st.download_button(
            "Télécharger le Markdown actuel",
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
        value=st.session_state.get("last_input_urls", ""),
        placeholder=(
            "https://www.youtube.com/watch?v=VIDEO_ID\n"
            "https://youtu.be/VIDEO_ID\n"
            "https://www.youtube.com/shorts/VIDEO_ID"
        ),
        height=220,
    )

    entered_url_count = count_entered_urls(raw_input)
    if entered_url_count > 25:
        st.info(LARGE_BATCH_MESSAGE)

    st.caption(
        f"Traitement par lots : {MAX_VIDEOS_PER_RUN} vidéos maximum par session, "
        f"{TRANSCRIPT_DELAY_SECONDS}s entre deux vidéos, pause de "
        f"{BATCH_COOLDOWN_SECONDS}s toutes les 10 vidéos."
    )

    can_resume = st.session_state["summary"]["unprocessed"] > 0
    col_generate, col_resume = st.columns(2)

    with col_generate:
        generate_clicked = st.button("Générer le Markdown", type="primary")
    with col_resume:
        resume_clicked = st.button("Reprendre le traitement", disabled=not can_resume)

    if generate_clicked:
        if entered_url_count == 0:
            st.error("Collez au moins une URL YouTube avant de générer le Markdown.")
        else:
            with st.spinner("Traitement du prochain lot en cours..."):
                run_state = start_processing_run(raw_input)
                update_session_from_run_state(run_state)

    if resume_clicked:
        with st.spinner("Reprise du traitement en cours..."):
            run_state = resume_processing_run()
            update_session_from_run_state(run_state)

    if (
        st.session_state["results"]
        and raw_input != st.session_state["last_input_urls"]
    ):
        st.info("L'entrée a changé depuis la dernière génération.")

    render_status_message()
    run_state = st.session_state.get("run_state") or {}
    if (
        run_state.get("stopped_reason") == "throttled"
        and run_state.get("message") != THROTTLING_MESSAGE
    ):
        st.warning(THROTTLING_MESSAGE)

    if CURRENT_RUN_PATH.exists():
        st.caption(f"Sauvegarde locale : {CURRENT_RUN_PATH}")

    render_results()


if __name__ == "__main__":
    main()
