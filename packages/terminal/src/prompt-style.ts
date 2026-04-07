import { ANSI } from "./ansi.js";
import { getTheme } from "./theme.js";

function applyStyle(style: string, value: string): string {
  return `${style}${value}${ANSI.RESET}`;
}

export function stylePromptMessage(message: string): string {
  return applyStyle(getTheme().primary, message);
}

export function stylePromptTitle(title?: string): string | undefined {
  if (!title) {
    return title;
  }
  return applyStyle(getTheme().heading, title);
}

export function stylePromptHint(hint?: string): string | undefined {
  if (!hint) {
    return hint;
  }
  return applyStyle(getTheme().muted, hint);
}