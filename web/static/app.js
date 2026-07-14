(() => {
  const form = document.getElementById("demo-form");
  const urlInput = document.getElementById("url");
  const stepsSelect = document.getElementById("max-steps");
  const langSelect = document.getElementById("language");
  const brandPrimary = document.getElementById("brand-primary");
  const brandName = document.getElementById("brand-name");
  const submitBtn = document.getElementById("submit");
  const statusEl = document.getElementById("status");
  const progressBar = document.getElementById("progress-bar");
  const timeline = document.getElementById("timeline");
  const videoEl = document.getElementById("video");
  const placeholder = document.getElementById("placeholder");
  const errEl = document.getElementById("err");
  const actions = document.getElementById("actions");
  const stageLabel = document.getElementById("stage-label");

  let pollTimer = null;

  function setStatus(status) {
    statusEl.className = "status-pill " + (status || "queued");
    statusEl.textContent = status || "idle";
  }

  function showError(msg) {
    if (!msg) {
      errEl.classList.remove("show");
      errEl.textContent = "";
      return;
    }
    errEl.textContent = msg;
    errEl.classList.add("show");
  }

  function setProgress(pct) {
    progressBar.style.width = Math.max(0, Math.min(100, pct)) + "%";
  }

  function roleBadge(role, proof) {
    const bits = [];
    if (role === "focus") bits.push('<span class="chip focus">focus</span>');
    if (role === "result") bits.push('<span class="chip result">result</span>');
    if (proof === "url_changed" || proof === "same_page" || proof === "proven") {
      bits.push('<span class="chip ok">proven</span>');
    } else if (proof === "unproven" || proof === "failed") {
      bits.push('<span class="chip bad">' + escapeHtml(proof) + '</span>');
    }
    return bits.join(" ");
  }

  function renderTimeline(steps, meta) {
    timeline.innerHTML = "";
    if (!steps || !steps.length) {
      timeline.innerHTML = '<li><div class="n">…</div><div><div class="t">Waiting for capture…</div><div class="d">Screenshots and narration appear here.</div></div></li>';
      return;
    }
    if (meta && (meta.proven_clicks != null || meta.accuracy_ok != null)) {
      const li = document.createElement("li");
      const acc = meta.accuracy_ok ? "ok" : "bad";
      li.innerHTML = `
        <div class="n">✓</div>
        <div>
          <div class="t">Accuracy</div>
          <div class="d">
            <span class="chip ${acc}">${meta.accuracy_ok ? "accuracy_ok" : "needs review"}</span>
            <span class="chip result">${escapeHtml(String(meta.proven_clicks || 0))} proven clicks</span>
            <span class="chip">${escapeHtml(String(meta.failed_clicks || 0))} skipped no-ops</span>
          </div>
        </div>`;
      timeline.appendChild(li);
    }
    steps.forEach((s, i) => {
      const li = document.createElement("li");
      const role = s.frame_role || "result";
      const proof = s.proof_status || "";
      li.innerHTML = `
        <div class="n">${i + 1}</div>
        <div>
          <div class="t">${escapeHtml(s.title || s.url || "Step")} ${roleBadge(role, proof)}</div>
          <div class="d">${escapeHtml(s.subtitle || s.label || s.action || "")}</div>
        </div>`;
      timeline.appendChild(li);
    });
  }

  function escapeHtml(str) {
    return String(str)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function setActions(jobId, ready) {
    actions.innerHTML = "";
    if (!ready) return;
    const v = document.createElement("a");
    v.href = `/api/jobs/${jobId}/video`;
    v.download = `shipvideo-${jobId}.mp4`;
    v.textContent = "Download MP4";
    const s = document.createElement("a");
    s.href = `/api/jobs/${jobId}/srt`;
    s.download = `shipvideo-${jobId}.srt`;
    s.textContent = "Download SRT";
    actions.appendChild(v);
    actions.appendChild(s);
    const g = document.createElement("a");
    g.href = `/api/jobs/${jobId}/gif`;
    g.download = `shipvideo-${jobId}.gif`;
    g.textContent = "Download GIF preview";
    actions.appendChild(g);
    const th = document.createElement("a");
    th.href = `/api/jobs/${jobId}/thumbnail`;
    th.download = `shipvideo-${jobId}-thumb.jpg`;
    th.textContent = "Download thumbnail";
    actions.appendChild(th);
    const ch = document.createElement("a");
    ch.href = `/api/jobs/${jobId}/chapters`;
    ch.download = `shipvideo-${jobId}-chapters.txt`;
    ch.textContent = "Download chapters";
    actions.appendChild(ch);
    const yt = document.createElement("a");
    yt.href = `/api/jobs/${jobId}/youtube`;
    yt.download = `shipvideo-${jobId}-youtube.txt`;
    yt.textContent = "YouTube description";
    actions.appendChild(yt);
    const sz = document.createElement("a");
    sz.href = `/api/jobs/${jobId}/sizzle`;
    sz.download = `shipvideo-${jobId}-sizzle.mp4`;
    sz.textContent = "Download vertical sizzle";
    actions.appendChild(sz);
  }

  async function pollJob(jobId) {
    try {
      const res = await fetch(`/api/jobs/${jobId}`);
      if (!res.ok) throw new Error("Failed to load job");
      const job = await res.json();
      setStatus(job.status);
      stageLabel.textContent = job.stage || job.status || "";

      const stages = ["queued", "starting", "capture_start", "capture_done", "render_start", "render_done", "done"];
      const idx = Math.max(0, stages.indexOf(job.stage));
      setProgress(job.status === "done" ? 100 : Math.round(((idx + 1) / stages.length) * 100));

      const steps = (job.result && job.result.steps) || [];
      if (steps.length) renderTimeline(steps, job.result || {});

      if (job.status === "done") {
        clearInterval(pollTimer);
        pollTimer = null;
        submitBtn.disabled = false;
        placeholder.style.display = "none";
        videoEl.style.display = "block";
        videoEl.src = `/api/jobs/${jobId}/video?t=${Date.now()}`;
        videoEl.load();
        setActions(jobId, true);
        showError(null);
        return;
      }
      if (job.status === "failed") {
        clearInterval(pollTimer);
        pollTimer = null;
        submitBtn.disabled = false;
        showError(job.error || "Job failed");
        setProgress(100);
      }
    } catch (e) {
      showError(e.message || String(e));
    }
  }

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    showError(null);
    setActions(null, false);
    videoEl.removeAttribute("src");
    videoEl.style.display = "none";
    placeholder.style.display = "block";
    renderTimeline([]);
    setProgress(5);
    setStatus("queued");
    stageLabel.textContent = "queued";
    submitBtn.disabled = true;

    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }

    const url = urlInput.value.trim();
    const max_steps = parseInt(stepsSelect.value, 10) || 10;
    const language = (langSelect && langSelect.value) || "en";
    const brand_primary = (brandPrimary && brandPrimary.value) || "";
    const brand_name = (brandName && brandName.value) || "";

    try {
      const res = await fetch("/api/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url, max_steps, language, brand_primary, brand_name, export_sizzle: true }),
      });
      if (!res.ok) {
        const t = await res.text();
        throw new Error(t || "Could not start job");
      }
      const job = await res.json();
      setStatus(job.status);
      pollTimer = setInterval(() => pollJob(job.id), 1200);
      pollJob(job.id);
    } catch (e) {
      submitBtn.disabled = false;
      showError(e.message || String(e));
      setStatus("failed");
    }
  });

  renderTimeline([]);
})();
