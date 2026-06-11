import { useState, useEffect, useRef } from "react";
import "./App.css";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------
const APP_VERSION = "0.1.0";

function fileIcon(name) {
  if (name.endsWith(".json")) return "📁";
  if (name.endsWith(".tsv")) return "📊";
  if (name.endsWith(".xlsx")) return "📊";
  if (name.endsWith(".pdf")) return "📄";
  if (name.endsWith(".png")) return "🖼";
  if (name.endsWith(".fasta") || name.endsWith(".fa") || name.endsWith(".fna")) return "🧬";
  if (name.endsWith(".txt")) return "📝";
  if (name.endsWith(".html")) return "🌐";
  return "📁";
}

function fmtSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

// Green when a defined genotype is assigned; amber otherwise. Reuses the
// run-status palette so no new CSS is introduced.
function genoClass(assigned) {
  return assigned ? "run-status run-status-done" : "run-status run-status-running";
}

// ---------------------------------------------------------------------------
// App
// ---------------------------------------------------------------------------
export default function App() {
  const [projects, setProjects] = useState([]);
  const [projectsLoading, setProjectsLoading] = useState(true);
  const [newProjectName, setNewProjectName] = useState("");
  const [creatingProject, setCreatingProject] = useState(false);
  const [activeProject, setActiveProject] = useState("");
  const [addPath, setAddPath] = useState({});
  const [sraText, setSraText] = useState({});
  const [addStatus, setAddStatus] = useState({});
  const [inputsByProj, setInputsByProj] = useState({});
  const uploadProjRef = useRef("");
  const uploadInputRef = useRef(null);
  const [expanded, setExpanded] = useState({});
  const [samples, setSamples] = useState({});
  const [checkedKeys, setCheckedKeys] = useState({});
  const [openResults, setOpenResults] = useState({});
  const [sampleResults, setSampleResults] = useState({});  // key -> {loading,status,present,files}
  const [genoTables, setGenoTables] = useState({});        // key -> parsed geno-table
  const [activeRun, setActiveRun] = useState(null);
  const [queueInfo, setQueueInfo] = useState({ total: 0, done: 0 });

  // GenoFLU run config
  const [pident, setPident] = useState("98");
  const [genofluDb, setGenofluDb] = useState("");

  const [running, setRunning] = useState(false);
  const [jobId, setJobId] = useState(null);
  const [jobStatus, setJobStatus] = useState("idle");
  const [logLines, setLogLines] = useState([]);
  const [settingsDraft, setSettingsDraft] = useState({});
  const [folderBrowser, setFolderBrowser] = useState({ open: false, path: "", parent: null, entries: [], loading: false, error: "" });
  const [currentStep, setCurrentStep] = useState("");

  const [showSettings, setShowSettings] = useState(false);
  const [showProjects, setShowProjects] = useState(true);
  const [showRun, setShowRun] = useState(true);
  const [showResults, setShowResults] = useState(true);
  const [showLogs, setShowLogs] = useState(true);

  const [selectedResultKey, setSelectedResultKey] = useState(null);

  const logRef = useRef(null);
  const eventSourceRef = useRef(null);

  useEffect(() => {
    fetch("./api/config")
      .then((r) => r.json())
      .then((cfg) => {
        setGenofluDb(cfg.genoflu_db || "");
        if (cfg.pident_threshold != null) setPident(String(cfg.pident_threshold));
        setSettingsDraft(cfg);
      })
      .catch(() => {});
    loadProjects();
    fetch("./api/jobs")
      .then((r) => r.json())
      .then((jobs) => {
        const live = jobs.find((j) => j.status === "running");
        if (live) {
          setJobId(live.id);
          setJobStatus("running");
          setRunning(true);
          let samp = null;
          const m = (live.name || "").match(/^(.*?)\/(.*?) — /);
          if (m) {
            samp = { project: m[1], sample: m[2] };
            setActiveRun(samp);
          }
          streamLogUntilDone(live.id, samp, () => {});
        }
      })
      .catch(() => {});
  }, []);

  function loadProjects() {
    setProjectsLoading(true);
    fetch("./api/projects")
      .then((r) => r.json())
      .then((data) => {
        setProjects(data);
        setProjectsLoading(false);
      })
      .catch(() => setProjectsLoading(false));
  }

  async function createProject() {
    const name = newProjectName.trim();
    if (!name || creatingProject) return;
    setCreatingProject(true);
    try {
      const res = await fetch("./api/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        window.alert(`Could not create project: ${detail.detail || res.status}`);
        return;
      }
      const created = await res.json().catch(() => ({}));
      setNewProjectName("");
      loadProjects();
      if (created.name) {
        const n = created.name;
        setExpanded((e) => ({ ...e, [n]: true }));
        setActiveProject(n);
        await Promise.all([fetchSamples(n), loadInputs(n)]);
      }
    } finally {
      setCreatingProject(false);
    }
  }

  useEffect(() => {
    const el = logRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    if (nearBottom) el.scrollTop = el.scrollHeight;
  }, [logLines]);

  useEffect(() => {
    if (!projects.length) {
      if (activeProject) setActiveProject("");
      return;
    }
    if (!activeProject || !projects.find((p) => p.name === activeProject)) {
      const first = projects[0].name;
      setActiveProject(first);
      if (inputsByProj[first] === undefined) loadInputs(first);
    }
  }, [projects]);

  function fetchSamples(name) {
    return fetch(`./api/projects/${encodeURIComponent(name)}/samples`)
      .then((r) => r.json())
      .then((data) => setSamples((s) => ({ ...s, [name]: data })))
      .catch(() => setSamples((s) => ({ ...s, [name]: [] })));
  }

  function toggleProject(name) {
    const isExpanded = expanded[name];
    setExpanded((e) => ({ ...e, [name]: !isExpanded }));
    setActiveProject(name);
    if (!isExpanded) {
      if (!samples[name]) fetchSamples(name);
      loadInputs(name);
    }
  }

  function selectProject(name) {
    setActiveProject(name);
    if (inputsByProj[name] === undefined) loadInputs(name);
  }

  function loadInputs(name) {
    return fetch(`./api/projects/${encodeURIComponent(name)}/inputs`)
      .then((r) => r.json())
      .then((data) => setInputsByProj((m) => ({ ...m, [name]: data })))
      .catch(() => setInputsByProj((m) => ({ ...m, [name]: { files: [], count: 0, total_bytes: 0 } })));
  }

  const setStat = (name, msg) => setAddStatus((m) => ({ ...m, [name]: msg }));

  async function refreshAfterLoad(name) {
    await Promise.all([fetchSamples(name), loadInputs(name)]);
    loadProjects();
  }

  async function linkLocal(name) {
    const path = (addPath[name] || "").trim();
    if (!path) return;
    setStat(name, "Linking…");
    try {
      const res = await fetch(`./api/projects/${encodeURIComponent(name)}/link-local`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) { setStat(name, `Import failed: ${data.detail || res.status}`); return; }
      setStat(name, `Linked ${data.linked} file${data.linked === 1 ? "" : "s"}.`);
      setAddPath((m) => ({ ...m, [name]: "" }));
      await refreshAfterLoad(name);
    } catch (e) {
      setStat(name, `Import failed: ${e.message}`);
    }
  }

  function pickFiles(name) {
    uploadProjRef.current = name;
    uploadInputRef.current?.click();
  }

  async function uploadFiles(name, fileList) {
    const files = Array.from(fileList || []).filter(
      (f) => /\.(fasta|fa|fna|fas)$/i.test(f.name)
    );
    if (!name || !files.length) return;
    const fd = new FormData();
    files.forEach((f) => fd.append("files", f));
    setStat(name, `Uploading ${files.length} file${files.length === 1 ? "" : "s"}…`);
    try {
      const res = await fetch(`./api/projects/${encodeURIComponent(name)}/upload`, { method: "POST", body: fd });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) { setStat(name, `Upload failed: ${data.detail || res.status}`); return; }
      setStat(name, `Uploaded ${data.uploaded} file${data.uploaded === 1 ? "" : "s"}.`);
      await refreshAfterLoad(name);
    } catch (e) {
      setStat(name, `Upload failed: ${e.message}`);
    }
  }

  function parseAccessions(text) {
    return (text || "").split(/[\s,]+/).map((s) => s.trim()).filter(Boolean);
  }

  async function sraDownload(name) {
    const accessions = parseAccessions(sraText[name]);
    if (!accessions.length) return;
    setStat(name, `Resolving ${accessions.length} accession${accessions.length === 1 ? "" : "s"}…`);
    setShowLogs(true);
    try {
      const res = await fetch(`./api/projects/${encodeURIComponent(name)}/sra/download`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ accessions }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) { setStat(name, `Download failed: ${data.detail || res.status}`); return; }
      setStat(name, "Downloading… progress shows in the Pipeline Log below.");
      setSraText((m) => ({ ...m, [name]: "" }));
      setJobId(data.job_id);
      setJobStatus("running");
      setLogLines([]);
      streamLogUntilDone(data.job_id, null, () => {
        setStat(name, "Download finished — see files below.");
        refreshAfterLoad(name);
      });
    } catch (e) {
      setStat(name, `Download failed: ${e.message}`);
    }
  }

  async function deleteInput(name, filename) {
    if (!window.confirm(`Remove ${filename} from this project's download/ folder?`)) return;
    try {
      await fetch(`./api/projects/${encodeURIComponent(name)}/inputs/${encodeURIComponent(filename)}`, { method: "DELETE" });
      await refreshAfterLoad(name);
    } catch (e) {
      setStat(name, `Delete failed: ${e.message}`);
    }
  }

  const sampleKey = (project, s) => `${project}::${s.sample}`;
  const isActive = (project, s) =>
    activeRun && activeRun.project === project && activeRun.sample === s.sample;

  function toggleChecked(project, s) {
    const key = sampleKey(project, s);
    setCheckedKeys((m) => {
      const next = { ...m };
      if (next[key]) delete next[key];
      else next[key] = { project, ...s };
      return next;
    });
  }

  function loadSampleResults(project, s) {
    const key = sampleKey(project, s);
    setSampleResults((m) => ({ ...m, [key]: { ...(m[key] || {}), loading: true } }));
    fetch(`./api/projects/${encodeURIComponent(project)}/samples/${encodeURIComponent(s.sample)}/geno-results`)
      .then((r) => r.json())
      .then((data) => setSampleResults((m) => ({ ...m, [key]: { loading: false, ...data } })))
      .catch(() => setSampleResults((m) => ({ ...m, [key]: { loading: false, present: false, status: "none", files: [] } })));
  }

  function loadGenoTable(project, s) {
    const key = sampleKey(project, s);
    setGenoTables((m) => ({ ...m, [key]: { ...(m[key] || {}), loading: true } }));
    fetch(`./api/projects/${encodeURIComponent(project)}/samples/${encodeURIComponent(s.sample)}/geno-table`)
      .then((r) => r.json())
      .then((data) => setGenoTables((m) => ({ ...m, [key]: { loading: false, ...data } })))
      .catch(() => setGenoTables((m) => ({ ...m, [key]: { loading: false, present: false, result: {} } })));
  }

  function toggleResults(project, s) {
    const key = sampleKey(project, s);
    const willOpen = !openResults[key];
    setOpenResults((m) => ({ ...m, [key]: willOpen }));
    if (willOpen) {
      setSelectedResultKey(key);
      setShowResults(true);
      if (!sampleResults[key]) loadSampleResults(project, s);
      if (!genoTables[key]) loadGenoTable(project, s);
    }
  }

  async function runSamples(list) {
    if (running || !list.length) return;
    setShowLogs(true);
    setQueueInfo({ total: list.length, done: 0 });
    for (let i = 0; i < list.length; i++) {
      await runOne(list[i]);
      setQueueInfo({ total: list.length, done: i + 1 });
    }
    setActiveRun(null);
  }

  function runSelected() {
    runSamples(Object.values(checkedKeys));
  }

  function runOne(samp) {
    return new Promise((resolve) => {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
      }
      setRunning(true);
      setActiveRun({ project: samp.project, sample: samp.sample });
      setJobStatus("running");
      setLogLines([]);
      setCurrentStep("");
      const key = sampleKey(samp.project, samp);
      setSampleResults((m) => ({ ...m, [key]: { ...(m[key] || {}), status: "running" } }));
      setOpenResults((m) => ({ ...m, [key]: true }));
      setSelectedResultKey(key);

      fetch("./api/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          project: samp.project,
          fasta: samp.fasta,
          pident: pident ? parseFloat(pident) : null,
          genoflu_db: genofluDb.trim() || null,
        }),
      })
        .then((r) => (r.ok ? r.json() : r.json().then((e) => { throw new Error(e.detail || "Run failed"); })))
        .then(({ job_id }) => {
          setJobId(job_id);
          streamLogUntilDone(job_id, samp, resolve);
        })
        .catch((err) => {
          setLogLines((prev) => [...prev, `ERROR: ${err.message}`]);
          setRunning(false);
          setJobStatus("failed");
          resolve();
        });
    });
  }

  function streamLogUntilDone(id, samp, done) {
    const es = new EventSource(`./api/jobs/${id}/log`);
    eventSourceRef.current = es;
    es.onmessage = (evt) => {
      const data = evt.data;
      if (data === "[DONE]") {
        es.close();
        setRunning(false);
        fetch(`./api/jobs/${id}`)
          .then((r) => r.json())
          .then((job) => {
            setJobStatus(job.status);
            setCurrentStep("");
            if (samp) {
              loadSampleResults(samp.project, samp);
              loadGenoTable(samp.project, samp);
            }
            loadProjects();
          })
          .catch(() => {})
          .finally(() => done());
      } else {
        setLogLines((prev) => [...prev, data]);
        if (/Step \d+:/i.test(data) ||
            /GenoFLU genotype/i.test(data) ||
            /Pipeline completed/i.test(data)) {
          setCurrentStep(data.trim().replace(/^#+\s*/, ""));
        }
      }
    };
    es.onerror = () => {
      es.close();
      setRunning(false);
      setJobStatus("failed");
      done();
    };
  }

  function browseDirs(path) {
    setFolderBrowser((s) => ({ ...s, loading: true, error: "" }));
    fetch(`./api/browse-dirs?path=${encodeURIComponent(path || "")}`)
      .then((r) => (r.ok ? r.json() : r.json().then((e) => { throw new Error(e.detail || "Cannot open folder"); })))
      .then((d) => setFolderBrowser((s) => ({ ...s, path: d.path, parent: d.parent, entries: d.entries, loading: false })))
      .catch((err) => setFolderBrowser((s) => ({ ...s, loading: false, error: err.message })));
  }
  function openFolderBrowser() {
    setFolderBrowser({ open: true, path: "", parent: null, entries: [], loading: true, error: "" });
    browseDirs(settingsDraft.projects_root || "");
  }
  function chooseFolder() {
    setSettingsDraft((d) => ({ ...d, projects_root: folderBrowser.path }));
    setFolderBrowser((s) => ({ ...s, open: false }));
  }

  function saveSettings() {
    fetch("./api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        genoflu_db: settingsDraft.genoflu_db,
        pident_threshold: settingsDraft.pident_threshold != null && settingsDraft.pident_threshold !== ""
          ? parseFloat(settingsDraft.pident_threshold) : null,
        projects_root: settingsDraft.projects_root,
      }),
    })
      .then((r) => r.json())
      .then(() => {
        setGenofluDb(settingsDraft.genoflu_db || "");
        if (settingsDraft.pident_threshold != null && settingsDraft.pident_threshold !== "")
          setPident(String(settingsDraft.pident_threshold));
        loadProjects();
      })
      .catch(() => {});
  }

  const logLineClass = (line) => {
    if (line.startsWith("$ ")) return "log-line cmd";
    if (line.startsWith("ERROR") || line.startsWith("error")) return "log-line error";
    if (line === "[DONE]") return "log-line done";
    return "log-line";
  };

  const statusText = { idle: "idle", running: "running", succeeded: "succeeded", failed: "failed" }[jobStatus];

  // Results pane data for the selected sample.
  const resTable = selectedResultKey ? genoTables[selectedResultKey] : null;
  const resFiles = selectedResultKey ? sampleResults[selectedResultKey] : null;
  const resResult = resTable?.result || {};
  const resQc = resTable?.qc || {};
  const resProv = resTable?.provenance || {};
  const resGenotype = resResult.genotype || (resTable?.present ? "Not assigned" : "—");
  const resAssigned = !!resResult.complete;

  return (
    <div className="app">
      <input
        ref={uploadInputRef}
        type="file"
        multiple
        accept=".fasta,.fa,.fna,.fas"
        style={{ display: "none" }}
        onChange={(e) => {
          const files = Array.from(e.target.files);
          e.target.value = "";
          if (uploadProjRef.current) uploadFiles(uploadProjRef.current, files);
        }}
      />
      {/* ── Header ─────────────────────────────────────────────── */}
      <header className="app-header">
        <div className="app-brand">
          <img className="app-logo" src="./genoflu_icon.svg" alt="Influenza genotyping icon" />
          <div>
            <h1>
              GenoFLU <span className="version-tag">v{APP_VERSION}</span>
            </h1>
            <p>Influenza A (H5N1 2.3.4.4b) whole-genome genotyping by per-segment BLAST</p>
          </div>
        </div>
        <div className="status-pill">
          <span className="dot" data-state={jobStatus} />
          <span>{statusText}</span>
        </div>
      </header>

      <main className="layout">
        {/* ── Status strip ─────────────────────────────────────── */}
        <section className="status-strip">
          <div className="status-item">
            <span className="status-label">Selected</span>
            <span className="status-value">
              {Object.keys(checkedKeys).length
                ? `${Object.keys(checkedKeys).length} sample${Object.keys(checkedKeys).length > 1 ? "s" : ""}`
                : "—"}
            </span>
          </div>
          <div className="status-item">
            <span className="status-label">Genotype (selected)</span>
            <span className="status-value">{resGenotype}</span>
          </div>
          <div className="status-item">
            <span className="status-label">Identity threshold</span>
            <span className="status-value cap">≥ {pident || "98"}%</span>
          </div>
          <div className="status-item">
            <span className="status-label">Job</span>
            <span className="status-value cap">
              {jobStatus === "running" ? <><span className="pulse-dot" />running</> : statusText}
            </span>
          </div>
        </section>

        {/* ════ Settings ════ */}
        <div className="row-header">
          <h2>Settings</h2>
          <button className="ghost" onClick={() => {
            if (!showSettings) {
              fetch("./api/config").then((r) => r.json()).then(setSettingsDraft).catch(() => {});
            }
            setShowSettings(!showSettings);
          }}>
            {showSettings ? "Hide" : "Show"}
          </button>
        </div>
        {showSettings && (
          <div className="row-grid row-grid-single">
            <section className="panel">
              <div className="form-section">
                <label className="form-label">GenoFLU reference database (dependencies dir)</label>
                <input
                  placeholder="(leave blank to use the conda package's bundled reference DB)"
                  value={settingsDraft.genoflu_db || ""}
                  onChange={(e) => setSettingsDraft((d) => ({ ...d, genoflu_db: e.target.value }))}
                />
                <div className="form-hint">
                  Optional. A GenoFLU <code>dependencies/</code> dir containing <code>fastas/</code> and
                  <code> genotype_key.xlsx</code>. Blank uses the pinned, version-recorded package DB.
                  To update genotypes, run <code>deploy/update_genoflu.sh</code>.
                </div>
              </div>
              <div className="form-section">
                <label className="form-label">Percent-identity threshold (%)</label>
                <input
                  type="number" min="50" max="100" step="0.1"
                  placeholder="98"
                  value={settingsDraft.pident_threshold ?? ""}
                  onChange={(e) => setSettingsDraft((d) => ({ ...d, pident_threshold: e.target.value }))}
                />
                <div className="form-hint">GenoFLU's default is 98.0% — the USDA-VS surveillance value.</div>
              </div>
              <div className="form-section">
                <label className="form-label">Personal projects root</label>
                <div style={{ display: "flex", gap: 6 }}>
                  <input
                    style={{ flex: 1 }}
                    value={settingsDraft.projects_root || ""}
                    onChange={(e) => setSettingsDraft((d) => ({ ...d, projects_root: e.target.value }))}
                  />
                  <button type="button" className="ghost" onClick={openFolderBrowser}>Browse…</button>
                </div>
                {Array.isArray(settingsDraft.recent_projects_roots) && settingsDraft.recent_projects_roots.length > 0 && (
                  <select
                    style={{ marginTop: 6, width: "100%" }}
                    value=""
                    onChange={(e) => { if (e.target.value) setSettingsDraft((d) => ({ ...d, projects_root: e.target.value })); }}
                  >
                    <option value="">↻ Recent roots…</option>
                    {settingsDraft.recent_projects_roots.map((r) => (
                      <option key={r} value={r}>{r}</option>
                    ))}
                  </select>
                )}
                <div className="form-hint">New projects are created under this root. Shared projects at /srv/kapurlab/projects/ are always visible. Click Save to apply.</div>
              </div>
              <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
                <button onClick={saveSettings}>Save</button>
              </div>
            </section>
          </div>
        )}

        {/* ════ Projects & Samples ════ */}
        <div className="row-header">
          <h2>Projects &amp; Samples</h2>
          <button className="ghost" onClick={() => setShowProjects(!showProjects)}>
            {showProjects ? "Hide" : "Show"}
          </button>
        </div>
        {showProjects && (
          <div className="row-grid row-grid-split">
            {/* LEFT — project / sample browser */}
            <section className="panel">
              <div className="panel-header">
                <h2>Projects</h2>
                <div className="panel-actions">
                  <button className="ghost action" onClick={loadProjects}>↻ Refresh</button>
                </div>
              </div>
              <div className="row">
                <input
                  placeholder="New project name (e.g. HPAI_surveillance)"
                  value={newProjectName}
                  onChange={(e) => setNewProjectName(e.target.value.replace(/\s+/g, "_"))}
                  onKeyDown={(e) => { if (e.key === "Enter") createProject(); }}
                  disabled={creatingProject}
                  title="Spaces become underscores. Letters, digits, _ - . are allowed. Shared with the sibling GUIs."
                />
                <button onClick={createProject} disabled={creatingProject || !newProjectName.trim()}>
                  {creatingProject ? "Creating…" : "Create"}
                </button>
              </div>
              <div className="form-hint" style={{ marginTop: -4, marginBottom: 8 }}>
                Created under your personal projects root — also visible in vSNP, Kraken and AMR GUIs. Add assembled influenza-A FASTA files (all 8 segments) to the project’s <code>download/</code> folder.
              </div>
              <div className="list project-list">
                {projectsLoading && <div className="loading-text">Loading projects…</div>}
                {!projectsLoading && projects.length === 0 && (
                  <div className="note">No projects found. Check Settings for the projects path.</div>
                )}
                {projects.map((proj) => (
                  <div
                    key={proj.name}
                    className={`list-item ${activeRun?.project === proj.name || activeProject === proj.name ? "active" : ""}`}
                  >
                    <div className="item-top" onClick={() => toggleProject(proj.name)}>
                      <span className="expand-icon">{expanded[proj.name] ? "▾" : "▸"}</span>
                      <div className="list-title" title={proj.name}>{proj.name}</div>
                      <span className={`scope-badge scope-${proj.scope}`}>{proj.scope}</span>
                    </div>
                    {proj.path && <div className="list-path" title={proj.path}>{proj.path}</div>}
                    <div className="list-meta">
                      {proj.fasta_count} FASTA
                      {proj.geno_runs?.length > 0 &&
                        ` · ${proj.geno_runs.length} GenoFLU run${proj.geno_runs.length > 1 ? "s" : ""}`}
                    </div>
                    {expanded[proj.name] && (
                      <div className="sample-list">
                        {!samples[proj.name] && <div className="loading-text">Loading samples…</div>}
                        {samples[proj.name]?.length === 0 && (
                          <div className="empty-msg" style={{ paddingLeft: 4 }}>
                            No FASTA files yet — add some from the <strong>Inputs</strong> pane on the right.
                          </div>
                        )}
                        {samples[proj.name]?.map((s) => {
                          const key = sampleKey(proj.name, s);
                          const res = sampleResults[key];
                          const hasRun = proj.geno_runs?.includes(s.sample);
                          const status = res?.status || (hasRun ? "done" : "none");
                          const checked = !!checkedKeys[key];
                          const open = !!openResults[key];
                          const statusLabel =
                            status === "running" ? "● running" : status === "done" ? "✓ results" : "not run";
                          return (
                          <div
                            key={s.fasta}
                            className={`sample-item ${isActive(proj.name, s) ? "active" : ""}`}
                          >
                            <div className="sample-name-row" style={{ display: "flex", alignItems: "center", gap: 8 }}>
                              <input
                                type="checkbox"
                                checked={checked}
                                onChange={() => toggleChecked(proj.name, s)}
                                title="Select for batch run"
                              />
                              <div
                                className="sample-name"
                                title={`${s.sample} — click to show results`}
                                style={{ flex: 1, cursor: "pointer" }}
                                onClick={() => toggleResults(proj.name, s)}
                              >
                                {s.sample}
                              </div>
                              <span className="read-badge badge-pe">FASTA</span>
                              <span
                                className={`run-status run-status-${status}`}
                                title={`Run status: ${status}`}
                                style={{ fontSize: 11, whiteSpace: "nowrap" }}
                              >
                                {statusLabel}
                              </span>
                              <button
                                className="ghost"
                                style={{ fontSize: 11 }}
                                onClick={() => toggleResults(proj.name, s)}
                                title="Show/hide results for this sample"
                              >
                                {open ? "▾" : "▸"}
                              </button>
                            </div>
                            <div className="sample-files">
                              <div className="sample-file-row">
                                <span className="file-name" title={s.fasta_name}>{s.fasta_name}</span>
                                <span className="file-size">{fmtSize(s.fasta_size)}</span>
                              </div>
                            </div>
                            {open && (
                              <div className="sample-results-inline" style={{ marginTop: 6, paddingLeft: 22 }}>
                                <div style={{ display: "flex", gap: 8, marginBottom: 4 }}>
                                  <button
                                    className="ghost action"
                                    disabled={running}
                                    onClick={() => runSamples([{ project: proj.name, ...s }])}
                                  >
                                    {status === "done" ? "↻ Re-run this sample" : "▶ Run GenoFLU"}
                                  </button>
                                  <button className="ghost action" onClick={() => { loadSampleResults(proj.name, s); loadGenoTable(proj.name, s); }}>
                                    ↻ Refresh
                                  </button>
                                  <button className="ghost action" onClick={() => { setSelectedResultKey(key); setShowResults(true); }}>
                                    View results ↓
                                  </button>
                                </div>
                                {res?.loading ? (
                                  <div className="loading-text">Loading results…</div>
                                ) : !res || !res.present || (res.files || []).length === 0 ? (
                                  <div className="empty-msg" style={{ paddingLeft: 0 }}>
                                    {status === "running"
                                      ? "Running… results will appear here when finished."
                                      : "No GenoFLU results yet for this sample."}
                                  </div>
                                ) : (
                                  <div className="results-list">
                                    {res.files.map((f) => {
                                      const base = `./api/projects/${encodeURIComponent(proj.name)}/file?path=${encodeURIComponent(f.path)}`;
                                      return (
                                        <div key={f.name} className="results-item">
                                          <span className="result-icon">{fileIcon(f.name)}</span>
                                          {f.openable ? (
                                            <a className="result-name result-link" href={`${base}&inline=1`}
                                               target="_blank" rel="noopener noreferrer" title={`Open ${f.name}`}>
                                              {f.label || f.name}
                                            </a>
                                          ) : (
                                            <a className="result-name result-link" href={`${base}&inline=0`}
                                               title={`Download ${f.name}`}>
                                              {f.label || f.name}
                                            </a>
                                          )}
                                          <span className="result-size">{fmtSize(f.size)}</span>
                                          <a className="result-download" href={`${base}&inline=0`} title={`Download ${f.name}`}>⬇</a>
                                        </div>
                                      );
                                    })}
                                  </div>
                                )}
                              </div>
                            )}
                          </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </section>

            {/* RIGHT — Inputs + batch selection */}
            <div style={{ display: "flex", flexDirection: "column", gap: 20, minWidth: 0 }}>
              <section className="panel">
                <div className="panel-header">
                  <h2>Inputs</h2>
                  {projects.length > 0 && (
                    <select
                      value={activeProject}
                      onChange={(e) => selectProject(e.target.value)}
                      title="Project to add FASTA files to"
                      style={{ width: "auto", maxWidth: "60%", padding: "6px 10px" }}
                    >
                      {projects.map((p) => (
                        <option key={p.name} value={p.name}>{p.name}</option>
                      ))}
                    </select>
                  )}
                </div>
                {!activeProject ? (
                  <div className="empty-msg">
                    Create a project first (top of the Projects panel), then import or upload assembled influenza-A FASTA files into it.
                  </div>
                ) : (
                  <div className="input-columns">
                    <div className="input-column">
                      <h3>Bring Your Own Assembly</h3>
                      <div className="row" style={{ margin: 0 }}>
                        <input
                          placeholder="/srv/kapurlab/… folder or a .fasta file"
                          value={addPath[activeProject] || ""}
                          onChange={(e) => setAddPath((m) => ({ ...m, [activeProject]: e.target.value }))}
                          onKeyDown={(e) => { if (e.key === "Enter") linkLocal(activeProject); }}
                        />
                        <button className="ghost action" onClick={() => linkLocal(activeProject)} disabled={!(addPath[activeProject] || "").trim()}>Link</button>
                      </div>
                      <div className="form-hint">Symlinks every assembled FASTA found — no copying. One multi-segment FASTA per sample.</div>

                      <div className="block">
                        <h3>Upload / Drag &amp; Drop</h3>
                        <div
                          className="dropzone"
                          onDragOver={(e) => e.preventDefault()}
                          onDrop={(e) => { e.preventDefault(); uploadFiles(activeProject, e.dataTransfer.files); }}
                        >
                          <button type="button" onClick={() => pickFiles(activeProject)}>Choose Files</button>
                          <span className="drop-hint">Or drop .fasta / .fa / .fna files here</span>
                        </div>
                        {addStatus[activeProject] && <div className="note" style={{ marginBottom: 0 }}>{addStatus[activeProject]}</div>}
                      </div>

                      {inputsByProj[activeProject]?.files?.length > 0 && (
                        <div className="block">
                          <h3 style={{ display: "flex", alignItems: "center", gap: 8 }}>
                            <span style={{ flex: 1 }}>
                              Files in download/
                              <span className="muted" style={{ marginLeft: 6, fontWeight: 400, fontSize: 12 }}>
                                ({inputsByProj[activeProject].count}, {fmtSize(inputsByProj[activeProject].total_bytes)})
                              </span>
                            </span>
                            <button className="ghost" style={{ fontSize: 11, padding: "2px 8px" }} onClick={() => loadInputs(activeProject)} title="Refresh">Refresh</button>
                          </h3>
                          <div className="input-files">
                            {inputsByProj[activeProject].files.map((f) => (
                              <div key={f.name} className="input-file-row">
                                <span className="file-name" title={f.name} style={{ flex: 1 }}>
                                  {f.is_fasta ? "🧬 " : ""}{f.name}
                                </span>
                                <span className="file-size">{fmtSize(f.size)}</span>
                                <button className="ghost" style={{ fontSize: 11, padding: "2px 7px" }} title="Remove from download/" onClick={() => deleteInput(activeProject, f.name)}>✕</button>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>

                    <div className="input-column">
                      <h3>SRA Download</h3>
                      <textarea
                        rows={6}
                        placeholder={"SRR/ERR/DRR accessions (one per line)"}
                        value={sraText[activeProject] || ""}
                        onChange={(e) => setSraText((m) => ({ ...m, [activeProject]: e.target.value }))}
                        style={{ resize: "vertical", fontFamily: "inherit" }}
                      />
                      <button
                        style={{ width: "100%" }}
                        onClick={() => sraDownload(activeProject)}
                        disabled={!parseAccessions(sraText[activeProject]).length || running}
                      >
                        Download{parseAccessions(sraText[activeProject]).length ? ` (${parseAccessions(sraText[activeProject]).length})` : ""}
                      </button>
                      <div className="form-hint">Downloads reads into download/ (shared with the sibling tools). GenoFLU itself needs an assembled FASTA — assemble reads upstream first.</div>
                    </div>
                  </div>
                )}
              </section>

              <section className="panel">
                <div className="panel-header">
                  <h2>Selected for run</h2>
                  {Object.keys(checkedKeys).length > 0 && (
                    <button className="ghost action" onClick={() => setCheckedKeys({})}>Clear</button>
                  )}
                </div>
                {Object.keys(checkedKeys).length === 0 ? (
                  <div className="empty-msg">
                    Check one or more samples on the left, then run them as a batch from “Run GenoFLU” below.
                    Click a sample’s name to view its results.
                  </div>
                ) : (
                  <div className="selection-box">
                    <div className="sel-title">{Object.keys(checkedKeys).length} sample(s) queued</div>
                    {Object.entries(checkedKeys).map(([key, samp]) => (
                      <div key={key} className="sel-row" style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <span className="sel-name" style={{ flex: 1 }}>{samp.sample}</span>
                        <span className="muted" style={{ fontSize: 11 }}>{samp.project}</span>
                        <button className="ghost" style={{ fontSize: 11 }}
                                onClick={() => toggleChecked(samp.project, samp)} title="Remove from batch">✕</button>
                      </div>
                    ))}
                  </div>
                )}
              </section>
            </div>
          </div>
        )}

        {/* ════ Run GenoFLU ════ */}
        <div className="row-header">
          <h2>Run GenoFLU</h2>
          <button className="ghost" onClick={() => setShowRun(!showRun)}>
            {showRun ? "Hide" : "Show"}
          </button>
        </div>
        {showRun && (
          <div className="row-grid row-grid-split">
            {/* LEFT — configure & run */}
            <section className="panel">
              <h2>Configure &amp; Run</h2>

              <div className="form-section">
                <label className="form-label">Percent-identity threshold (%)</label>
                <input
                  type="number" min="50" max="100" step="0.1"
                  placeholder="98"
                  value={pident}
                  onChange={(e) => setPident(e.target.value)}
                  disabled={running}
                />
                <div className="note" style={{ marginTop: 4 }}>
                  A segment counts as a match when its top BLASTN hit is at or above this identity.
                  GenoFLU's default (and the USDA-VS surveillance value) is <code>98</code>.
                </div>
              </div>

              <div className="form-section">
                <label className="form-label">GenoFLU reference DB (optional override)</label>
                <input
                  placeholder="(blank = pinned conda package DB; version recorded in provenance)"
                  value={genofluDb}
                  onChange={(e) => setGenofluDb(e.target.value)}
                  disabled={running}
                />
                <div className="note" style={{ marginTop: 4 }}>
                  Leave blank for the reproducible bundled reference set. Update genotypes with
                  <code> deploy/update_genoflu.sh</code>.
                </div>
              </div>

              <button
                className="run-btn"
                onClick={runSelected}
                disabled={running || Object.keys(checkedKeys).length === 0}
              >
                {running
                  ? `Running… ${queueInfo.total > 1 ? `(${queueInfo.done}/${queueInfo.total})` : ""}`
                  : `▶ Run selected${Object.keys(checkedKeys).length ? ` (${Object.keys(checkedKeys).length})` : ""}`}
              </button>
              {Object.keys(checkedKeys).length === 0 && (
                <div className="note">Check one or more samples on the left to enable the run. (Or use “Run GenoFLU” under any sample.)</div>
              )}
            </section>

            {/* RIGHT — current run status */}
            <section className="panel">
              <div className="panel-header">
                <h2>Current run</h2>
                {jobId && <span className="muted" style={{ fontSize: 12 }}>job {jobId.slice(0, 8)}</span>}
              </div>
              {activeRun ? (
                <div className="selection-box">
                  <div className="sel-title">
                    {jobStatus === "running" ? "Running" : jobStatus === "succeeded" ? "Done" : jobStatus}
                    {queueInfo.total > 1 ? ` — ${queueInfo.done}/${queueInfo.total} in batch` : ""}
                  </div>
                  <div><span className="sel-name">{activeRun.sample}</span></div>
                  <div style={{ marginTop: 2 }}>
                    <span className="muted">Project:</span> <strong>{activeRun.project}</strong>
                  </div>
                  {currentStep && <div className="muted" style={{ marginTop: 4 }}>{currentStep}</div>}
                  <div className="note" style={{ marginTop: 8 }}>
                    Files appear inline under each sample on the left; the genotype + per-segment table is in the Results section below.
                  </div>
                </div>
              ) : (
                <div className="empty-msg">
                  No active run. Select samples, set the threshold, and Run. Results for any sample are shown below.
                </div>
              )}
            </section>
          </div>
        )}

        {/* ════ Results ════ */}
        <div className="row-header">
          <h2>Results</h2>
          <button className="ghost" onClick={() => setShowResults(!showResults)}>
            {showResults ? "Hide" : "Show"}
          </button>
        </div>
        {showResults && (
          <div className="row-grid row-grid-single">
            <section className="panel">
              {!resTable ? (
                <div className="empty-msg">
                  Click a sample’s name in the Projects tree to load its GenoFLU results here.
                </div>
              ) : resTable.loading ? (
                <div className="loading-text">Loading results…</div>
              ) : !resTable.present ? (
                <div className="empty-msg">No GenoFLU results for {selectedResultKey?.split("::")[1]} yet — run it first.</div>
              ) : (
                <>
                  <div className="panel-header">
                    <h2>{selectedResultKey?.split("::")[1]}</h2>
                    <div className="panel-actions" style={{ display: "flex", gap: 10, alignItems: "center" }}>
                      <span className={genoClass(resAssigned)} style={{ fontSize: 12 }}>
                        {resGenotype}
                      </span>
                      {resResult.segments_matched != null && (
                        <span className="muted" style={{ fontSize: 12 }}>
                          {resResult.segments_matched} segment{resResult.segments_matched === 1 ? "" : "s"} ≥ {resProv?.options?.pident_threshold ?? pident}%
                        </span>
                      )}
                    </div>
                  </div>

                  {/* Genotype banner */}
                  <div className="selection-box" style={{ marginBottom: 12 }}>
                    <div className="sel-title">Genotype: {resGenotype}</div>
                    {resQc?.verdict && resQc.verdict !== "pass" && (
                      <div className="alert-banner" style={{ marginTop: 8 }}>
                        <strong>⚠ Input genome QC = {String(resQc.verdict).toUpperCase()}.</strong>{" "}
                        {(resQc.metrics?.num_seqs != null) ? `${resQc.metrics.num_seqs} sequence(s) present — ` : ""}
                        influenza A has 8 segments. A confident genotype requires all 8 segments above threshold.
                      </div>
                    )}
                  </div>

                  {/* Download links */}
                  {resFiles?.files?.length > 0 && (
                    <div className="results-list" style={{ marginBottom: 12 }}>
                      {resFiles.files
                        .filter((f) => ["report_pdf", "stats_xlsx", "genoflu_tsv", "genoflu_xlsx", "genoflu_result", "fasta_qc", "run_manifest"].includes(f.category))
                        .map((f) => {
                          const base = `./api/projects/${encodeURIComponent(selectedResultKey.split("::")[0])}/file?path=${encodeURIComponent(f.path)}`;
                          return (
                            <div key={f.name} className="results-item">
                              <span className="result-icon">{fileIcon(f.name)}</span>
                              <a className="result-name result-link" href={`${base}&inline=${f.openable ? 1 : 0}`}
                                 target={f.openable ? "_blank" : undefined} rel="noopener noreferrer">
                                {f.label || f.name}
                              </a>
                              <span className="result-size">{fmtSize(f.size)}</span>
                              <a className="result-download" href={`${base}&inline=0`} title={`Download ${f.name}`}>⬇</a>
                            </div>
                          );
                        })}
                    </div>
                  )}

                  {/* Per-segment table */}
                  {(resResult.segments || []).length === 0 ? (
                    <div className="note">
                      No segment matched a reference lineage above the identity threshold.
                    </div>
                  ) : (
                    <div style={{ overflowX: "auto" }}>
                      <table className="result-table" style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                        <thead>
                          <tr style={{ textAlign: "left", borderBottom: "2px solid var(--border, #ddd)" }}>
                            <th style={{ padding: "6px 8px" }}>Segment</th>
                            <th style={{ padding: "6px 8px" }}>Lineage</th>
                            <th style={{ padding: "6px 8px", textAlign: "right" }}>% identity</th>
                            <th style={{ padding: "6px 8px", textAlign: "right" }}>Mismatches</th>
                            <th style={{ padding: "6px 8px", textAlign: "right" }}>Avg depth</th>
                            <th style={{ padding: "6px 8px" }}>Top reference</th>
                          </tr>
                        </thead>
                        <tbody>
                          {resResult.segments.map((r, i) => (
                            <tr key={i} style={{ borderBottom: "1px solid var(--border, #eee)" }}>
                              <td style={{ padding: "5px 8px", fontWeight: 600 }}>{r.segment}</td>
                              <td style={{ padding: "5px 8px" }}>{r.lineage || "—"}</td>
                              <td style={{ padding: "5px 8px", textAlign: "right" }}>{r.percent_identity || "—"}</td>
                              <td style={{ padding: "5px 8px", textAlign: "right" }}>{r.mismatches || "—"}</td>
                              <td style={{ padding: "5px 8px", textAlign: "right" }}>{r.avg_depth || "—"}</td>
                              <td style={{ padding: "5px 8px", maxWidth: 320 }} title={r.reference}>{r.reference || "—"}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}

                  {/* Provenance / Options used disclosure */}
                  {resProv && Object.keys(resProv).length > 0 && (
                    <details style={{ marginTop: 14 }}>
                      <summary style={{ cursor: "pointer", fontWeight: 600 }}>Options used &amp; provenance</summary>
                      <div className="note" style={{ marginTop: 8 }}>
                        <div><strong>Command:</strong> <code style={{ wordBreak: "break-all" }}>{(resProv.command || []).join(" ")}</code></div>
                        {resProv.versions && (
                          <div style={{ marginTop: 6 }}>
                            <strong>Versions:</strong> GenoFLU {resProv.versions.genoflu || "?"} · {resProv.versions.blastn || "?"} · DB {resProv.versions.reference_db || "?"}
                          </div>
                        )}
                        {resProv.options && (
                          <div style={{ marginTop: 6 }}>
                            <strong>Threshold:</strong> ≥ {resProv.options.pident_threshold}% · DB source: {resProv.options.genoflu_db || "bundled"}
                          </div>
                        )}
                        {resProv.thresholds_note && (
                          <div style={{ marginTop: 6 }}>{resProv.thresholds_note}</div>
                        )}
                        {Array.isArray(resProv.iso_references) && (
                          <div style={{ marginTop: 6 }}>
                            <strong>Quality standards:</strong> {resProv.iso_references.map((r) => r.standard).join(", ")}
                          </div>
                        )}
                      </div>
                    </details>
                  )}
                </>
              )}
            </section>
          </div>
        )}

        {/* ════ Pipeline Log ════ */}
        <div className="row-header">
          <h2>Pipeline Log</h2>
          <button className="ghost" onClick={() => setShowLogs(!showLogs)}>
            {showLogs ? "Hide" : "Show"}
          </button>
        </div>
        {showLogs && (
          <div className="row-grid row-grid-single">
            <section className="panel">
              <div className="log-meta">
                <span className="dot" data-state={jobStatus} />
                <span style={{ fontWeight: 600 }}>
                  {jobStatus === "idle" && "Idle"}
                  {jobStatus === "running" && "Running"}
                  {jobStatus === "succeeded" && "Done"}
                  {jobStatus === "failed" && "Failed"}
                </span>
                {jobStatus === "running" && currentStep && (
                  <span className="log-step" title={currentStep}>— {currentStep}</span>
                )}
              </div>
              <div className="log" ref={logRef}>
                {logLines.length === 0 ? (
                  <span className="log-placeholder">
                    {jobStatus === "idle"
                      ? "Select a sample and click Run to start."
                      : "Waiting for output…"}
                  </span>
                ) : (
                  logLines.map((line, i) => (
                    <div key={i} className={logLineClass(line)}>{line}</div>
                  ))
                )}
              </div>
            </section>
          </div>
        )}
      </main>

      {folderBrowser.open && (
        <div
          onClick={() => setFolderBrowser((s) => ({ ...s, open: false }))}
          style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.45)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000 }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{ background: "var(--panel, #fff)", color: "inherit", borderRadius: 10, width: "min(640px, 92vw)", maxHeight: "80vh", display: "flex", flexDirection: "column", boxShadow: "0 10px 40px rgba(0,0,0,0.3)" }}
          >
            <div style={{ padding: "12px 16px", borderBottom: "1px solid var(--border, #ddd)", fontWeight: 700 }}>
              Select a projects root
            </div>
            <div style={{ padding: "10px 16px", display: "flex", gap: 6, alignItems: "center" }}>
              <button type="button" className="ghost" disabled={!folderBrowser.parent || folderBrowser.loading} onClick={() => browseDirs(folderBrowser.parent)}>↑ Up</button>
              <input
                style={{ flex: 1 }}
                value={folderBrowser.path}
                onChange={(e) => setFolderBrowser((s) => ({ ...s, path: e.target.value }))}
                onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); browseDirs(folderBrowser.path); } }}
              />
              <button type="button" className="ghost" onClick={() => browseDirs(folderBrowser.path)}>Go</button>
            </div>
            <div style={{ flex: 1, overflow: "auto", padding: "0 16px", minHeight: 160 }}>
              {folderBrowser.loading ? (
                <div className="note" style={{ padding: 12 }}>Loading…</div>
              ) : folderBrowser.error ? (
                <div className="note" style={{ padding: 12, color: "var(--danger, #c00)" }}>{folderBrowser.error}</div>
              ) : folderBrowser.entries.length === 0 ? (
                <div className="note" style={{ padding: 12 }}>No sub-folders here.</div>
              ) : (
                folderBrowser.entries.map((e) => (
                  <div
                    key={e.path}
                    onClick={() => browseDirs(e.path)}
                    style={{ padding: "7px 8px", cursor: "pointer", borderRadius: 6, display: "flex", gap: 8, alignItems: "center" }}
                    onMouseEnter={(ev) => (ev.currentTarget.style.background = "var(--panel-2, #f0f0f0)")}
                    onMouseLeave={(ev) => (ev.currentTarget.style.background = "transparent")}
                  >
                    <span>📁</span><span>{e.name}</span>
                  </div>
                ))
              )}
            </div>
            <div style={{ padding: "12px 16px", borderTop: "1px solid var(--border, #ddd)", display: "flex", justifyContent: "flex-end", gap: 8 }}>
              <button type="button" className="ghost" onClick={() => setFolderBrowser((s) => ({ ...s, open: false }))}>Cancel</button>
              <button type="button" onClick={chooseFolder} disabled={folderBrowser.loading || !folderBrowser.path}>Select this folder</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
