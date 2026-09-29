import { useState } from "react";
import type { CameraInfo, CourtRegion, FusionReport, SyncInfo } from "../types";

const REGION_LABEL: Record<CourtRegion, string> = {
  full: "Pista completa",
  left: "Mitad izquierda",
  right: "Mitad derecha",
};

function formatOffset(s: number): string {
  const sign = s > 0 ? "+" : s < 0 ? "−" : "";
  return `${sign}${Math.abs(s).toFixed(2)} s`;
}

function describeSync(sync: SyncInfo | null): { text: string; warn: boolean } {
  if (!sync) return { text: "Pendiente", warn: false };
  switch (sync.method) {
    case "reference":
      return { text: "Referencia de tiempo", warn: false };
    case "audio":
      return { text: `${formatOffset(sync.offset_s)} · detectado por el sonido`, warn: false };
    case "manual":
      return { text: `${formatOffset(sync.offset_s)} · ajustado a mano`, warn: false };
    case "clock":
      return { text: "Sincronizada (directo, reloj común)", warn: false };
    case "none":
      return {
        text:
          `Sin sincronizar (${sync.reason ?? "sin audio"})` +
          (sync.detected_offset_s !== undefined ? ` — posible desfase ${formatOffset(sync.detected_offset_s)}` : "") +
          ". Ajusta el desfase a mano.",
        warn: true,
      };
  }
}

function OffsetEditor({
  camera,
  onSave,
  onCancel,
}: {
  camera: CameraInfo;
  onSave: (update: { time_offset_s?: number; auto_sync?: boolean }) => Promise<void>;
  onCancel: () => void;
}) {
  const [value, setValue] = useState(String(camera.sync_info?.detected_offset_s ?? camera.sync_info?.offset_s ?? 0));
  const [saving, setSaving] = useState(false);

  async function save(update: { time_offset_s?: number; auto_sync?: boolean }) {
    setSaving(true);
    try {
      await onSave(update);
    } catch {
      // The parent shows the error; keep the editor open.
    } finally {
      setSaving(false);
    }
  }

  return (
    <form
      className="inline-edit"
      onSubmit={(e) => {
        e.preventDefault();
        save({ time_offset_s: Number(value) });
      }}
    >
      <input type="number" step="0.05" value={value} onChange={(e) => setValue(e.target.value)} autoFocus />
      <span className="helper-text">s</span>
      <button type="submit" className="btn btn-small btn-primary" disabled={saving}>
        Aplicar
      </button>
      {camera.source === "video" && (
        <button type="button" className="btn btn-small" disabled={saving} onClick={() => save({ auto_sync: true })}>
          Automático
        </button>
      )}
      <button type="button" className="btn btn-small" disabled={saving} onClick={onCancel}>
        Cancelar
      </button>
    </form>
  );
}

export default function CamerasPanel({
  cameras,
  report,
  canEdit,
  onUpdate,
}: {
  cameras: CameraInfo[];
  report: FusionReport | null;
  /** Sync can be changed once the analysis finished (and nothing is queued). */
  canEdit: boolean;
  onUpdate: (index: number, update: { time_offset_s?: number; auto_sync?: boolean }) => Promise<void>;
}) {
  const [editing, setEditing] = useState<number | null>(null);

  return (
    <>
      {report && (
        <p className="helper-text" style={{ marginTop: -6, marginBottom: 12 }}>
          {report.people} personas identificadas al unir {cameras.length} cámaras
          {report.overlap_merges > 0 && ` · ${report.overlap_merges} coincidencias en zonas compartidas`}
          {report.handoffs > 0 && ` · ${report.handoffs} relevos entre cámaras o cortes de seguimiento`}.
        </p>
      )}
      <table>
        <thead>
          <tr>
            <th>Cámara</th>
            <th>Zona</th>
            <th>Calibrada</th>
            <th>Sincronización</th>
            {canEdit && <th />}
          </tr>
        </thead>
        <tbody>
          {cameras.map((cam, i) => {
            const sync = describeSync(cam.sync_info);
            const isReference = i === 0;
            return (
              <tr key={cam.index}>
                <td>{cam.name}</td>
                <td>{REGION_LABEL[cam.region]}</td>
                <td>{cam.calibrated ? "Sí" : "No (encuadre completo)"}</td>
                <td>
                  {editing === cam.index ? (
                    <OffsetEditor
                      camera={cam}
                      onCancel={() => setEditing(null)}
                      onSave={async (update) => {
                        await onUpdate(cam.index, update);
                        setEditing(null);
                      }}
                    />
                  ) : (
                    <span className={`sync-method${sync.warn ? " warn" : ""}`}>{sync.text}</span>
                  )}
                </td>
                {canEdit && (
                  <td>
                    {!isReference && editing !== cam.index && (
                      <button type="button" className="link-button" onClick={() => setEditing(cam.index)}>
                        Ajustar
                      </button>
                    )}
                  </td>
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="helper-text">
        El desfase son los segundos que cada cámara empezó a grabar después de la primera. Al
        cambiarlo, las cámaras se vuelven a unir en unos segundos sin repetir el análisis de vídeo
        (se pierden los nombres y correcciones de jugadores).
      </p>
    </>
  );
}
