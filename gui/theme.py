"""
gui/theme.py

All color / style constants and the DearPyGui theme builder live here,
separate from the window layout code. Change a color once, it applies
everywhere.
"""

import dearpygui.dearpygui as dpg

BG_DARK      = (13, 17, 23)      # 0d1117
GRID_MAJOR   = (42, 58, 58)      # 2a3a3a
GRID_MINOR   = (26, 42, 42)      # 1a2a2a
TRACE_YELL   = (255, 204, 68)    # ffcc44  - CH1
TRACE_CYAN   = (76, 201, 240)    # 4cc9f0  - CH2
TRACE_VIL    = (205, 68, 255)    # cd44ff  - CH3
TRACE_GREEN  = (0, 212, 170)     # 00d4aa  - CH4
TEXT_MUTED   = (160, 176, 176)   # a0b0b0
TEXT_BRIGHT  = (224, 232, 232)   # e0e8e8
ACCENT_AMBER = (255, 204, 68)    # ffcc44
PANEL_BG     = (26, 42, 42)      # 1a2a2a
CURSOR_A     = (255, 255, 255)   # white  - cursor A (X1/Y1)
CURSOR_B     = (255, 90, 90)     # light red - cursor B (X2/Y2)


def rgba(c, a=255):
    return (*c, a)


def build_theme() -> int:
    """Create and return the theme id. Caller is responsible for binding it
    with dpg.bind_theme(...)."""
    with dpg.theme() as theme:
        with dpg.theme_component(dpg.mvAll):
            dpg.add_theme_color(dpg.mvThemeCol_WindowBg, rgba(BG_DARK), category=dpg.mvThemeCat_Core)
            dpg.add_theme_color(dpg.mvThemeCol_ChildBg, rgba(BG_DARK), category=dpg.mvThemeCat_Core)
            dpg.add_theme_color(dpg.mvThemeCol_Text, rgba(TEXT_MUTED), category=dpg.mvThemeCat_Core)
            dpg.add_theme_color(dpg.mvThemeCol_TitleBg, rgba(PANEL_BG), category=dpg.mvThemeCat_Core)
            dpg.add_theme_color(dpg.mvThemeCol_TitleBgActive, rgba(PANEL_BG), category=dpg.mvThemeCat_Core)
            dpg.add_theme_color(dpg.mvThemeCol_Button, rgba(PANEL_BG), category=dpg.mvThemeCat_Core)
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, rgba(GRID_MAJOR), category=dpg.mvThemeCat_Core)
            dpg.add_theme_color(dpg.mvThemeCol_FrameBg, rgba(PANEL_BG), category=dpg.mvThemeCat_Core)
            dpg.add_theme_style(dpg.mvStyleVar_WindowRounding, 6, category=dpg.mvThemeCat_Core)
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 4, category=dpg.mvThemeCat_Core)

        with dpg.theme_component(dpg.mvPlot):
            dpg.add_theme_color(dpg.mvPlotCol_PlotBg, rgba(BG_DARK), category=dpg.mvThemeCat_Plots)
            dpg.add_theme_color(dpg.mvPlotCol_FrameBg, rgba(BG_DARK), category=dpg.mvThemeCat_Plots)
            dpg.add_theme_color(dpg.mvPlotCol_Line, rgba(TRACE_GREEN), category=dpg.mvThemeCat_Plots)
            dpg.add_theme_color(dpg.mvPlotCol_AxisGrid, rgba(GRID_MAJOR), category=dpg.mvThemeCat_Plots)
            dpg.add_theme_color(dpg.mvPlotCol_PlotBorder, rgba(GRID_MAJOR), category=dpg.mvThemeCat_Plots)
            dpg.add_theme_color(dpg.mvPlotCol_LegendBg, rgba(PANEL_BG, 220), category=dpg.mvThemeCat_Plots)

    return theme


def build_series_theme(color) -> int:
    """A small item-level theme just for one line series' color, so different
    channels can be shown in different colors on the same plot."""
    with dpg.theme() as theme:
        with dpg.theme_component(dpg.mvLineSeries):
            dpg.add_theme_color(dpg.mvPlotCol_Line, rgba(color), category=dpg.mvThemeCat_Plots)
    return theme


def build_button_theme(color) -> int:
    """A small item-level theme just for one button's color - same idea as
    build_series_theme, but for mvThemeCol_Button instead of a plot line."""
    with dpg.theme() as theme:
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Button, rgba(color), category=dpg.mvThemeCat_Core)
    return theme
