import { ANSI } from "./ansi.js";

let activeStream: NodeJS.WriteStream | null = null;

export function registerActiveProgressLine(stream: NodeJS.WriteStream): void {
  if (!stream.isTTY) {
    return;
  }
  activeStream = stream;
}

export function clearActiveProgressLine(): void {
  if (!activeStream?.isTTY) {
    return;
  }
  activeStream.write(`\r${ANSI.CLEAR_LINE}`);
}

export function unregisterActiveProgressLine(stream?: NodeJS.WriteStream): void {
  if (!activeStream) {
    return;
  }
  if (stream && activeStream !== stream) {
    return;
  }
  activeStream = null;
}