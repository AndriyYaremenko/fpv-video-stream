"""OSD label formatting for the view stream (drawn by ffmpeg drawtext).

The persistent encoder's ffmpeg reads a reload=1 textfile the agent rewrites
per session/retune; this module builds that one line."""

IDLE_TEXT = "—"
DEFAULT_OSD_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def osd_text(freq_mhz, standard=None, channel=None):
    """One-line label: '<freq> MHz [<channel>] [· <standard>]'.
    e.g. osd_text(5800, 'PAL', 'F4') -> '5800 MHz F4 · PAL'."""
    label = f"{int(round(freq_mhz))} MHz"
    if channel:
        label += f" {channel}"
    if standard:
        label += f" · {standard}"
    return label
