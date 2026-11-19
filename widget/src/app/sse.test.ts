import { describe, expect, it } from "vitest";
import { SSEParser } from "./sse";

describe("SSEParser", () => {
  it("parses signal, resync and skips heartbeats", () => {
    const p = new SSEParser();
    const events = p.feed(
      "retry: 3000\n\n: heartbeat\n\nevent: signal\nid: 7\ndata: {\"t\":\"message.created\",\"s\":2}\n\nevent: resync\ndata: {}\n\n",
    );
    expect(events).toEqual([
      { event: "signal", id: "7", data: '{"t":"message.created","s":2}' },
      { event: "resync", id: undefined, data: "{}" },
    ]);
  });

  it("handles frames split across chunks and CRLF", () => {
    const p = new SSEParser();
    expect(p.feed("event: sig")).toEqual([]);
    expect(p.feed("nal\r\nid: 1\r\nda")).toEqual([]);
    expect(p.feed('ta: {"s":1}\r\n\r\n')).toEqual([{ event: "signal", id: "1", data: '{"s":1}' }]);
  });

  it("defaults event name to message and joins multi-line data", () => {
    const p = new SSEParser();
    expect(p.feed("data: a\ndata: b\n\n")).toEqual([{ event: "message", id: undefined, data: "a\nb" }]);
  });
});
