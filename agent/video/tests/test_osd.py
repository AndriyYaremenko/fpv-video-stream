from osd import osd_text, IDLE_TEXT, DEFAULT_OSD_FONT


def test_osd_text_formats():
    assert osd_text(3470) == "3470 MHz"
    assert osd_text(3470.4) == "3470 MHz"          # rounds to whole MHz
    assert osd_text(3470, "PAL") == "3470 MHz · PAL"
    assert osd_text(5800, "PAL", "F4") == "5800 MHz F4 · PAL"
    assert osd_text(5800, None, "F4") == "5800 MHz F4"


def test_osd_constants():
    assert IDLE_TEXT == "—"
    assert DEFAULT_OSD_FONT.endswith("DejaVuSans-Bold.ttf")
