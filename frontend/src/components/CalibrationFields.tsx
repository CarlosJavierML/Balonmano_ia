import { useState } from "react";
import type { PixelCorner } from "../types";

const CORNER_LABELS = [
  "Esquina superior izquierda",
  "Esquina superior derecha",
  "Esquina inferior derecha",
  "Esquina inferior izquierda",
];

const EMPTY: PixelCorner[] = [
  { x: 0, y: 0 },
  { x: 0, y: 0 },
  { x: 0, y: 0 },
  { x: 0, y: 0 },
];

export default function CalibrationFields({
  onChange,
}: {
  onChange: (corners: PixelCorner[] | undefined) => void;
}) {
  const [enabled, setEnabled] = useState(false);
  const [corners, setCorners] = useState<PixelCorner[]>(EMPTY);

  function toggle(next: boolean) {
    setEnabled(next);
    onChange(next ? corners : undefined);
  }

  function update(index: number, axis: "x" | "y", value: number) {
    const next = corners.map((c, i) => (i === index ? { ...c, [axis]: value } : c));
    setCorners(next);
    if (enabled) onChange(next);
  }

  return (
    <div className="form-group">
      <label>
        <input
          type="checkbox"
          checked={enabled}
          onChange={(e) => toggle(e.target.checked)}
          style={{ marginRight: 8 }}
        />
        Calibrar cámara manualmente (recomendado para distancias/velocidades precisas)
      </label>
      <p className="helper-text">
        Indica, en píxeles de la imagen, las 4 esquinas de la pista tal y como se ven desde tu
        cámara fija. Sin calibrar, la app asume que la cámara encuadra la pista completa de borde
        a borde, lo que puede distorsionar las medidas en los laterales.
      </p>
      {enabled && (
        <div className="corner-grid">
          {CORNER_LABELS.map((label, i) => (
            <div key={label}>
              <label style={{ fontWeight: 400 }}>{label}</label>
              <div className="corner-field">
                <input
                  type="number"
                  placeholder="x (px)"
                  value={corners[i].x}
                  onChange={(e) => update(i, "x", Number(e.target.value))}
                />
                <input
                  type="number"
                  placeholder="y (px)"
                  value={corners[i].y}
                  onChange={(e) => update(i, "y", Number(e.target.value))}
                />
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
