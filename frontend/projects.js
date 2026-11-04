/* 项目工作台：新建 / 打开 / 归档 / 恢复 / 重命名 / 删除 */
(function () {
  "use strict";

  var state = { status: "active", projects: [], detailId: null };
  var $ = function (id) { return document.getElementById(id); };

  function esc(text) {
    return String(text == null ? "" : text)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function showError(message) {
    var el = $("error");
    if (!message) { el.classList.add("hidden"); return; }
    el.textContent = message;
    el.classList.remove("hidden");
  }

  function api(url, options) {
    return fetch(url, options).then(function (r) {
      return r.json().then(function (data) {
        if (!r.ok) throw new Error(data.error || ("请求失败 " + r.status));
        return data;
      });
    });
  }

  function fmtTime(iso) {
    if (!iso) return "—";
    return String(iso).replace("T", " ");
  }

  function statusTag(status) {
    if (status === "done") return '<span class="tag t-ok">完成</span>';
    if (status === "running") return '<span class="tag t-run">进行中</span>';
    if (status === "queued") return '<span class="tag t-run">排队中</span>';
    if (status === "skipped") return '<span class="tag t-warn">已跳过</span>';
    if (status === "error") return '<span class="tag t-bad">失败</span>';
    return '<span class="tag t-mute">' + esc(status || "未知") + "</span>";
  }

  function jobTarget(job) {
    if (job.kind === "compare") return "/compare.html?job=" + job.id;
    return job.status === "done" ? "/fill.html?job=" + job.id : "/?job=" + job.id;
  }

  function loadProjects() {
    var query = state.status ? "?status=" + state.status : "";
    return api("/api/projects" + query).then(function (data) {
      state.projects = data.projects || [];
      renderGrid();
    });
  }

  function renderGrid() {
    var grid = $("grid");
    if (!state.projects.length) {
      grid.innerHTML = '<div class="empty">还没有项目——点右上角「新建项目」开始</div>';
      return;
    }
    grid.innerHTML = state.projects.map(function (p) {
      var ops = p.status === "active"
        ? '<button class="btn small" data-op="open" data-id="' + p.id + '">打开</button>' +
          '<button class="btn small" data-op="archive" data-id="' + p.id + '">归档</button>'
        : '<button class="btn small" data-op="restore" data-id="' + p.id + '">恢复</button>';
      ops += '<button class="btn small" data-op="rename" data-id="' + p.id + '">重命名</button>' +
        '<button class="btn small danger" data-op="delete" data-id="' + p.id + '">删除</button>';
      return '<div class="pcard' + (p.status === "archived" ? " archived" : "") + '" data-open="' + p.id + '">' +
        '<div class="name">' + esc(p.name) + "</div>" +
        (p.status === "archived" ? '<span class="badge archived">已归档</span>' : "") +
        (p.note ? '<p class="note">' + esc(p.note) + "</p>" : "") +
        '<div class="meta"><span>任务 ' + p.job_count + " 个</span>" +
        "<span>更新 " + esc(fmtTime(p.updated_at)) + "</span></div>" +
        '<div class="ops">' + ops + "</div></div>";
    }).join("");
  }

  // ---------------- 项目详情：任务列表 ----------------
  function openDetail(projectId) {
    state.detailId = projectId;
    api("/api/projects/" + projectId).then(function (p) {
      $("list-view").style.display = "none";
      $("detail-view").style.display = "";
      $("detail-title").textContent = p.name + "（任务 " + (p.jobs || []).length + " 个）";
      var jobs = p.jobs || [];
      if (!jobs.length) {
        $("detail-jobs").innerHTML = '<div class="empty">该项目还没有任务；到「需求解析」页上传文件时选择本项目即可</div>';
        return;
      }
      var rows = jobs.map(function (job) {
        return "<tr>" +
          "<td>" + statusTag(job.status) + "</td>" +
          '<td style="word-break:break-all">' + esc(job.filename) + "</td>" +
          "<td>" + esc(job.stage || "") + "</td>" +
          "<td>" + esc(fmtTime(job.created_at)) + "</td>" +
          '<td><a class="btn small" href="' + esc(jobTarget(job)) + '">查看</a></td>' +
          "</tr>";
      }).join("");
      var doneCount = jobs.filter(function (j) { return j.status === "done"; }).length;
      $("detail-jobs").innerHTML =
        '<div class="toolbar"><span class="hintline">完成后可整包导出：结果 JSON + 审核报告 + 招标文件 docx</span>' +
        '<div class="spacer"></div>' +
        '<button class="btn small primary" id="export-zip"' + (doneCount ? "" : " disabled") +
        '>导出项目文件包（' + doneCount + ' 个任务）</button></div>' +
        '<table class="tbl"><thead><tr>' +
        "<th>状态</th><th>文件</th><th>阶段</th><th>创建时间</th><th></th>" +
        "</tr></thead><tbody>" + rows + "</tbody></table>";
      var btn = document.getElementById("export-zip");
      if (btn && doneCount) {
        btn.addEventListener("click", function () {
          btn.disabled = true;
          btn.textContent = "打包中…";
          fetch("/api/projects/" + projectId + "/export", { method: "POST" })
            .then(function (r) {
              if (!r.ok) {
                return r.json().then(function (d) { throw new Error(d.error || "打包失败"); });
              }
              var disp = r.headers.get("Content-Disposition") || "";
              var m = /filename\*=UTF-8''([^;]+)/.exec(disp);
              var name = m ? decodeURIComponent(m[1]) : "项目导出.zip";
              return r.blob().then(function (blob) {
                var a = document.createElement("a");
                a.href = URL.createObjectURL(blob);
                a.download = name;
                a.click();
                URL.revokeObjectURL(a.href);
              });
            })
            .catch(function (err) { showError(err.message || String(err)); })
            .finally(function () {
              btn.disabled = false;
              btn.textContent = "导出项目文件包（" + doneCount + " 个任务）";
            });
        });
      }
    }).catch(function (err) { showError(err.message || String(err)); });
  }

  function backToList() {
    state.detailId = null;
    $("detail-view").style.display = "none";
    $("list-view").style.display = "";
    loadProjects().catch(function () {});
  }

  // ---------------- 项目操作 ----------------
  function projectAction(projectId, action, body) {
    return api("/api/projects/" + projectId + "/" + action, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
  }

  function onCardAction(op, projectId) {
    if (op === "open") { openDetail(projectId); return; }
    if (op === "archive") {
      projectAction(projectId, "archive").then(loadProjects)
        .catch(function (err) { showError(err.message || String(err)); });
      return;
    }
    if (op === "restore") {
      projectAction(projectId, "restore").then(loadProjects)
        .catch(function (err) { showError(err.message || String(err)); });
      return;
    }
    if (op === "rename") {
      var project = state.projects.filter(function (p) { return p.id === projectId; })[0];
      $("rn-name").value = project ? project.name : "";
      $("dlg-rename").dataset.id = projectId;
      $("dlg-rename").showModal();
      return;
    }
    if (op === "delete") {
      var name = (state.projects.filter(function (p) { return p.id === projectId; })[0] || {}).name || "";
      if (!window.confirm("删除项目「" + name + "」？项目下的任务与字段确认值将一并删除，不可恢复。")) return;
      projectAction(projectId, "delete").then(loadProjects)
        .catch(function (err) { showError(err.message || String(err)); });
    }
  }

  // ---------------- 对话框 ----------------
  function bindDialogs() {
    $("new-project").addEventListener("click", function () {
      $("np-name").value = "";
      $("np-note").value = "";
      $("dlg-new").showModal();
      $("np-name").focus();
    });
    $("np-cancel").addEventListener("click", function () { $("dlg-new").close(); });
    $("np-create").addEventListener("click", function () {
      var name = $("np-name").value.trim();
      if (!name) { $("np-name").focus(); return; }
      api("/api/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name, note: $("np-note").value }),
      }).then(function () {
        $("dlg-new").close();
        loadProjects().catch(function () {});
      }).catch(function (err) { showError(err.message || String(err)); });
    });
    $("rn-cancel").addEventListener("click", function () { $("dlg-rename").close(); });
    $("rn-save").addEventListener("click", function () {
      var id = $("dlg-rename").dataset.id;
      var name = $("rn-name").value.trim();
      if (!id || !name) return;
      projectAction(id, "rename", { name: name }).then(function () {
        $("dlg-rename").close();
        loadProjects().catch(function () {});
      }).catch(function (err) { showError(err.message || String(err)); });
    });
  }

  // ---------------- 事件绑定与初始化 ----------------
  function bindEvents() {
    $("status-seg").addEventListener("click", function (e) {
      var btn = e.target.closest("button");
      if (!btn) return;
      document.querySelectorAll("#status-seg button").forEach(function (b) { b.classList.remove("active"); });
      btn.classList.add("active");
      state.status = btn.dataset.status;
      loadProjects().catch(function () {});
    });
    $("grid").addEventListener("click", function (e) {
      var opBtn = e.target.closest("[data-op]");
      if (opBtn) {
        e.stopPropagation();
        onCardAction(opBtn.dataset.op, opBtn.dataset.id);
        return;
      }
      var card = e.target.closest("[data-open]");
      if (card) openDetail(card.dataset.open);
    });
    $("back").addEventListener("click", backToList);
    bindDialogs();
  }

  bindEvents();
  loadProjects().catch(function (err) { showError(err.message || String(err)); });

  // ?id=xxx：直接打开某项目详情
  (function () {
    var id = new URLSearchParams(window.location.search).get("id");
    if (id) openDetail(id);
  })();
})();
