import { useState, useRef, useCallback } from "react";

export type RecorderState = "idle" | "recording" | "processing" | "speaking";

export function useRecorder() {
  const [state, setState] = useState<RecorderState>("idle");
  const mediaRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);

  const start = useCallback(async () => {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const mimeType = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
      ? "audio/webm;codecs=opus"
      : "audio/webm";
    const recorder = new MediaRecorder(stream, { mimeType });
    chunksRef.current = [];
    recorder.ondataavailable = (e) => {
      if (e.data.size > 0) chunksRef.current.push(e.data);
    };
    mediaRef.current = recorder;
    recorder.start(250);
    setState("recording");
  }, []);

  const stop = useCallback((): Promise<Blob> => {
    return new Promise((resolve) => {
      const recorder = mediaRef.current;
      if (!recorder) return;
      recorder.onstop = () => {
        const blob = new Blob(chunksRef.current, { type: "audio/webm" });
        recorder.stream.getTracks().forEach((t) => t.stop());
        setState("processing");
        resolve(blob);
      };
      recorder.stop();
    });
  }, []);

  const setSpeaking = useCallback(() => setState("speaking"), []);
  const reset = useCallback(() => setState("idle"), []);

  return { state, start, stop, setSpeaking, reset };
}
