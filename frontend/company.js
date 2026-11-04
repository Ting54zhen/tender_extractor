// 企业档案：加载 / 编辑 / 保存
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };
  var profile = null;

  var BASIC = [
    ["name", "企业名称"], ["credit_code", "统一社会信用代码"], ["registered_capital", "注册资本"],
    ["founded", "成立日期"], ["address", "注册地址"], ["legal_person", "法定代表人"],
    ["contact_name", "联系人"], ["contact_phone", "联系电话"], ["business_scope", "经营范围"]
  ];
  var LIST_DEFS = {
    qualifications: { id: "qualifications", add: "add-q", fields: [["name", "资质名称*"], ["certificate_no", "证书编号"], ["issuer", "发证机构"], ["valid_until", "有效期至"], ["evidence", "扫描件位置/说明"]] },
    performances: { id: "performances", add: "add-p", fields: [["project_name", "项目名称*"], ["amount", "合同金额"], ["year", "年度"], ["client", "甲方"], ["evidence", "中标通知书/合同位置"]] },
    personnel: { id: "personnel", add: "add-r", fields: [["name", "姓名*"], ["role", "拟任角色"], ["title", "职称"], ["certificate", "执业证书"], ["years", "从业年限"]] }
  };

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

  function renderBasic() {
    var c = profile.company || {};
    $("basic").innerHTML = BASIC.map(function (pair) {
      return "<label>" + esc(pair[1]) +
        '<input data-basic="' + pair[0] + '" value="' + esc(c[pair[0]] || "") + '"></label>';
    }).join("");
  }

  function renderRow(section, item, index) {
    var def = LIST_DEFS[section];
    var cells = def.fields.map(function (f) {
      return "<label>" + esc(f[1]) +
        '<input data-f="' + f[0] + '" value="' + esc(item[f[0]] == null ? "" : item[f[0]]) + '"></label>';
    }).join("");
    return '<div class="row-item" data-index="' + index + '">' + cells +
      '<button class="btn small danger del" data-del="' + index + '">删除</button></div>';
  }

  function renderList(section) {
    var def = LIST_DEFS[section];
    var rows = profile[section] || [];
    $(def.id).innerHTML = rows.length
      ? rows.map(function (item, i) { return renderRow(section, item, i); }).join("")
      : '<div class="empty">还没有条目，点下方按钮添加</div>';
  }

  function renderEvidence() {
    var map = profile.evidence_index || {};
    var keys = Object.keys(map);
    $("evidence").innerHTML = keys.length
      ? keys.map(function (k) {
          return '<div class="kv-item">' +
            '<input data-k data-old-key="' + esc(k) + '" value="' + esc(k) + '">' +
            '<input data-v value="' + esc(map[k] || "") + '">' +
            '<button class="btn small danger" data-del-k="' + esc(k) + '">删</button></div>';
        }).join("")
      : '<div class="empty">还没有材料索引；投标书生成时会按资格要求自动建议要补的材料</div>';
  }

  function renderAll() {
    renderBasic();
    Object.keys(LIST_DEFS).forEach(renderList);
    renderEvidence();
  }

  function flashOk() {
    var el = $("ok");
    el.classList.add("show");
    setTimeout(function () { el.classList.remove("show"); }, 2500);
  }

  function bindListButtons() {
    Object.keys(LIST_DEFS).forEach(function (section) {
      var def = LIST_DEFS[section];
      $(def.add).addEventListener("click", function () {
        profile[section] = profile[section] || [];
        var blank = {};
        def.fields.forEach(function (f) { blank[f[0]] = ""; });
        profile[section].push(blank);
        renderList(section);
      });
      $(def.id).addEventListener("click", function (e) {
        var del = e.target.closest("[data-del]");
        if (!del) return;
        profile[section].splice(Number(del.dataset.del), 1);
        renderList(section);
      });
      // 输入即写入 profile（保存时统一提交）
      $(def.id).addEventListener("input", function (e) {
        var input = e.target.closest("input[data-f]");
        if (!input) return;
        var row = input.closest(".row-item");
        if (!row) return;
        profile[section][Number(row.dataset.index)][input.dataset.f] = input.value;
      });
    });
  }

  function bindEvidence() {
    $("add-e").addEventListener("click", function () {
      profile.evidence_index = profile.evidence_index || {};
      var name = "new_material_" + (Object.keys(profile.evidence_index).length + 1);
      profile.evidence_index[name] = "";
      renderEvidence();
    });
    $("evidence").addEventListener("click", function (e) {
      var del = e.target.closest("[data-del-k]");
      if (!del) return;
      delete profile.evidence_index[del.dataset.delK];
      renderEvidence();
    });
    // 值随输随记；key 失焦时改名（旧 key → 新 key）
    $("evidence").addEventListener("input", function (e) {
      if (e.target.hasAttribute("data-v")) {
        var keyInput = e.target.parentNode.querySelector("[data-k]");
        if (keyInput) profile.evidence_index[keyInput.dataset.oldKey] = e.target.value;
      }
    });
    $("evidence").addEventListener("change", function (e) {
      var keyInput = e.target.closest("input[data-k]");
      if (!keyInput) return;
      var oldKey = keyInput.dataset.oldKey;
      var newKey = keyInput.value.trim();
      if (!newKey || newKey === oldKey) { keyInput.value = oldKey; return; }
      if (profile.evidence_index[newKey] !== undefined) { keyInput.value = oldKey; return; }
      profile.evidence_index[newKey] = profile.evidence_index[oldKey] || "";
      delete profile.evidence_index[oldKey];
      keyInput.dataset.oldKey = newKey;
    });
  }

  function collect() {
    var c = {};
    document.querySelectorAll("[data-basic]").forEach(function (input) {
      c[input.dataset.basic] = input.value.trim();
    });
    var out = { company: c, evidence_index: {} };
    Object.keys(LIST_DEFS).forEach(function (section) {
      out[section] = (profile[section] || []).map(function (item) {
        var copy = {};
        Object.keys(item).forEach(function (k) { copy[k] = item[k]; });
        return copy;
      });
    });
    document.querySelectorAll("#evidence .kv-item").forEach(function (el) {
      var k = el.querySelector("[data-k]").value.trim();
      var v = el.querySelector("[data-v]").value.trim();
      if (k) out.evidence_index[k] = v;
    });
    return out;
  }

  function bindSave() {
    $("save").addEventListener("click", function () {
      api("/api/company", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(collect()),
      }).then(function (data) {
        profile = data.profile;
        renderAll();
        flashOk();
        showError("");
      }).catch(function (err) { showError(err.message || String(err)); });
    });
  }

  function load() {
    api("/api/company").then(function (data) {
      profile = data.profile;
      renderAll();
    }).catch(function (err) { showError(err.message || String(err)); });
  }

  bindListButtons();
  bindEvidence();
  bindSave();
  load();
})();
