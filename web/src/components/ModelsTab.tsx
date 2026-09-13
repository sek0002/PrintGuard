import { useState } from "react";
import { useStore } from "../store";

export function ModelsTab() {
  const { engine, send, isPending } = useStore();
  const active = engine?.settings.model_id ?? "default";
  const [selected, setSelected] = useState(active);
  const models = engine?.models ?? [];
  const model = models.find((item) => item.id === selected);
  const pending = isPending("settings.update");
  return (
    <div role="tabpanel" id="settings-panel-models" aria-labelledby="settings-tab-models" tabIndex={0} className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <span className="label block">Active model</span>
          <span className="text-sm text-accent">{models.find((item) => item.id === active)?.name ?? active}</span>
        </div>
        <button className="btn" disabled={pending || isPending("models.refresh")} onClick={() => send({ cmd: "models.refresh" })}>
          Refresh list
        </button>
      </div>
      <label className="label block" htmlFor="detection-model">Installed models ({models.length})</label>
      <select id="detection-model" className="field w-full" value={selected} disabled={pending} onChange={(event) => setSelected(event.target.value)}>
        {models.map((item) => <option key={item.id} value={item.id} disabled={!!item.error}>{item.name}{item.error ? " (unavailable)" : ""}</option>)}
      </select>
      {model && (
        <div className="space-y-3 rounded border border-line-0 p-3 text-xs text-text-1">
          <div className="flex flex-wrap gap-2"><span className="chip">{model.status}</span><span className="chip max-w-full whitespace-normal">{model.license}</span></div>
          <p className="leading-relaxed">{model.notes || "Uses your monitor sensitivity, threshold and alert settings."}</p>
          {model.source && <a href={model.source} target="_blank" rel="noreferrer" className="text-accent underline">Model source and documentation ↗</a>}
          {model.error && <p className="text-bad">{model.error}</p>}
        </div>
      )}
      {model?.recommendation && (
        <div className="space-y-2 rounded border border-line-0 p-3 text-xs text-text-1">
          <span className="label block">{model.recommendation.recommended ? "Provisional tested preset" : "Trial preset: not recommended by this test"}</span>
          <p>Sensitivity {model.recommendation.sensitivity.toFixed(1)} · Threshold {model.recommendation.threshold.toFixed(2)}</p>
          <p>{model.recommendation.consecutive != null ? `Consecutive detections ${model.recommendation.consecutive}` : "No tested consecutive count; keeps your current count."}</p>
          <p className="leading-relaxed">{model.recommendation.summary}</p>
          <span className="block text-text-2">Tested {model.recommendation.evaluated_at}. Printer actions and cooldowns are unchanged.</span>
          {selected === active && <button className="btn" disabled={pending} onClick={() => send({ cmd: "settings.update", patch: { model_presets_enabled: true } })}>Apply tested settings</button>}
        </div>
      )}
      <label className="flex items-start gap-2 text-xs text-text-1">
        <input type="checkbox" checked={engine?.settings.model_presets_enabled ?? false} disabled={pending} onChange={(event) => send({ cmd: "settings.update", patch: { model_presets_enabled: event.target.checked } })} />
        <span>Use each model’s tested preset when switching. Enabling this applies sensitivity, threshold and any tested consecutive count to all monitors. Models without a preset keep the current values.</span>
      </label>
      <p className="text-xs leading-relaxed text-text-2">The selected model applies to all monitors. Detection pauses while it loads and resumes automatically. A failed switch keeps the previous model. Experimental models may need different sensitivity and alert thresholds.</p>
      <div className="flex items-center justify-between gap-3">
        <span role="status" className="text-xs text-text-2">{pending ? "Loading and checking model…" : selected === active ? "This model is active" : "Ready to switch"}</span>
        <button className="btn btn-primary" disabled={pending || selected === active || !model || !!model.error} onClick={() => send({ cmd: "settings.update", patch: { model_id: selected } })}>
          {pending ? "Switching…" : "Use model"}
        </button>
      </div>
    </div>
  );
}
