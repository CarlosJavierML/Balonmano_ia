import { useEffect, useRef } from "react";

/** Live preview of this device's camera stream. */
export default function DeviceCameraPreview({ stream }: { stream: MediaStream }) {
  const ref = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    if (ref.current && ref.current.srcObject !== stream) {
      ref.current.srcObject = stream;
      ref.current.play().catch(() => undefined);
    }
  }, [stream]);
  return <video ref={ref} className="device-preview" muted playsInline autoPlay />;
}
