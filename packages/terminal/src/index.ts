// @arkhe/terminal — public API re-exports

export { ANSI, strip, visibleLength } from "./ansi.js";

export {
  type Theme,
  DEFAULT_THEME,
  DARK_THEME,
  LIGHT_THEME,
  applyTheme,
  getTheme,
} from "./theme.js";

export { gradient, rgbToAnsi256, hexToRgb, namedColor } from "./palette.js";

export { type TableOptions, renderTable, renderRow } from "./table.js";

export { type ProgressLineOptions, ProgressLine } from "./progress-line.js";

export { StreamWriter } from "./stream-writer.js";

export {
  sanitizeForTerminal,
  truncate,
  wrapWords,
  indent,
  pad,
} from "./safe-text.js";

export { hyperlink, isHyperlinkSupported } from "./links.js";

export {
  type NoteStyle,
  type NoteRenderOptions,
  renderNote,
} from "./note.js";

export {
  type HealthStatus,
  renderHealthBadge,
  renderHealthTable,
} from "./health-style.js";
