"""PDF report generation: per-session summary with physical stats, tactical
events timeline and heatmaps, using matplotlib for charts and reportlab for
document layout."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless rendering, no display server needed
import matplotlib.pyplot as plt
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.models import Event, Match, PlayerMatchStat


def render_heatmap_image(grid: list[list[int]], out_path: Path, title: str) -> None:
    fig, ax = plt.subplots(figsize=(6, 3))
    ax.imshow(grid, cmap="hot", interpolation="nearest", aspect="auto")
    ax.set_title(title)
    ax.set_xticks([])
    ax.set_yticks([])
    fig.tight_layout()
    fig.savefig(out_path, dpi=140)
    plt.close(fig)


def _distance_chart(player_stats: list[PlayerMatchStat], out_path: Path) -> None:
    labels = [s.label or f"#{s.track_id}" for s in player_stats]
    distances = [round(s.distance_m) for s in player_stats]

    fig, ax = plt.subplots(figsize=(6, 3))
    ax.bar(labels, distances, color="#1f6f43")
    ax.set_ylabel("Distancia (m)")
    ax.set_title("Distancia recorrida por jugador")
    plt.setp(ax.get_xticklabels(), rotation=30, ha="right")
    fig.tight_layout()
    fig.savefig(out_path, dpi=140)
    plt.close(fig)


def _format_ts(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"


EVENT_LABELS = {
    "pase": "Pase",
    "perdida": "Pérdida de balón",
    "cambio_posesion": "Cambio de posesión",
    "tiro": "Tiro",
    "gol": "Gol",
}


TABLE_STYLE = TableStyle(
    [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f6f43")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
    ]
)

SIDE_LABELS = {"left": "izquierda", "right": "derecha"}


def _team_summary_table(team_summary: dict) -> Table:
    teams = sorted(team_summary)
    rows = [["", *[f"Equipo {t}" for t in teams]]]
    for label, key, fmt in (
        ("Jugadores", "players", "{}"),
        ("Posesión", "possession_pct", "{} %"),
        ("Pases", "pases", "{}"),
        ("Pérdidas", "perdidas", "{}"),
        ("Tiros", "tiros", "{}"),
        ("Goles", "goles", "{}"),
    ):
        rows.append([label, *[fmt.format(team_summary[t].get(key, 0)) for t in teams]])

    table = Table(rows, hAlign="LEFT", colWidths=[4 * cm] + [3.5 * cm] * len(teams))
    table.setStyle(TABLE_STYLE)
    # Color chip on each team header, using the detected jersey color.
    for col, team in enumerate(teams, start=1):
        color = team_summary[team].get("color")
        if color:
            table.setStyle(TableStyle([("LINEBELOW", (col, 0), (col, 0), 4, colors.HexColor(color))]))
    return table


def build_match_report(
    match: Match,
    player_stats: list[PlayerMatchStat],
    events: list[Event],
    out_path: Path,
    tmp_dir: Path,
) -> Path:
    tmp_dir.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(str(out_path), pagesize=A4, topMargin=1.5 * cm, bottomMargin=1.5 * cm)
    story = []

    story.append(Paragraph(f"Informe de entrenamiento — {match.name}", styles["Title"]))
    story.append(
        Paragraph(
            f"Modalidad: {match.court_type.upper()} · Fuente: {match.source_mode} · "
            f"Duración: {_format_ts(match.duration_s or 0)} · Fecha: {match.created_at:%d/%m/%Y %H:%M}",
            styles["Normal"],
        )
    )
    story.append(Spacer(1, 0.5 * cm))

    if match.team_summary:
        story.append(Paragraph("Resumen por equipos", styles["Heading2"]))
        story.append(_team_summary_table(match.team_summary))
        story.append(
            Paragraph(
                "Equipos asignados automáticamente por el color de la camiseta.",
                styles["Italic"],
            )
        )
        story.append(Spacer(1, 0.5 * cm))

    if player_stats:
        chart_path = tmp_dir / f"distance_{match.id}.png"
        _distance_chart(player_stats, chart_path)
        story.append(Image(str(chart_path), width=16 * cm, height=8 * cm))
        story.append(Spacer(1, 0.5 * cm))

        story.append(Paragraph("Estadísticas físicas por jugador", styles["Heading2"]))
        table_data = [["Jugador", "Equipo", "Distancia (m)", "Vel. media (km/h)", "Vel. máx (km/h)", "Sprints"]]
        for s in player_stats:
            table_data.append(
                [
                    s.label or f"#{s.track_id}",
                    s.team or "—",
                    f"{s.distance_m:.0f}",
                    f"{s.avg_speed_kmh:.1f}",
                    f"{s.max_speed_kmh:.1f}",
                    str(s.sprint_count),
                ]
            )
        table = Table(table_data, hAlign="LEFT")
        table.setStyle(TABLE_STYLE)
        story.append(table)
        story.append(Spacer(1, 0.5 * cm))

        for s in player_stats:
            if s.heatmap_path and Path(s.heatmap_path).exists():
                story.append(Paragraph(f"Mapa de calor — {s.label or f'#{s.track_id}'}", styles["Heading3"]))
                story.append(Image(s.heatmap_path, width=14 * cm, height=7 * cm))
                story.append(Spacer(1, 0.3 * cm))
    else:
        story.append(Paragraph("No se detectaron jugadores en el vídeo analizado.", styles["Normal"]))

    story.append(PageBreak())
    story.append(Paragraph("Eventos tácticos detectados", styles["Heading2"]))
    if events:
        event_table_data = [["Minuto", "Evento", "Detalle"]]
        for e in sorted(events, key=lambda ev: ev.timestamp_s):
            detail_parts = []
            if e.track_id_from is not None:
                detail_parts.append(f"desde #{e.track_id_from}")
            if e.track_id_to is not None:
                detail_parts.append(f"hacia #{e.track_id_to}")
            if e.meta and "side" in e.meta:
                detail_parts.append(f"portería {SIDE_LABELS.get(e.meta['side'], e.meta['side'])}")
            if e.meta and "team" in e.meta:
                detail_parts.append(f"equipo {e.meta['team']}")
            event_table_data.append(
                [_format_ts(e.timestamp_s), EVENT_LABELS.get(e.event_type, e.event_type), ", ".join(detail_parts)]
            )
        event_table = Table(event_table_data, hAlign="LEFT")
        event_table.setStyle(TABLE_STYLE)
        story.append(event_table)
    else:
        story.append(Paragraph("No se detectaron eventos tácticos en esta sesión.", styles["Normal"]))

    story.append(Spacer(1, 0.8 * cm))
    story.append(
        Paragraph(
            "Nota: la detección de eventos tácticos es heurística (posición y velocidad del balón) y puede "
            "contener falsos positivos/negativos, especialmente sin calibración manual de la cámara. "
            "Ver docs/ROADMAP.md para el plan de mejora con modelos entrenados específicamente para balonmano.",
            styles["Italic"],
        )
    )

    doc.build(story)
    return out_path
