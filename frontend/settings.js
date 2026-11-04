// 可视化设置：读取 / 保存用户覆盖层
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };
  var state = { settings: {}, overrides: {} };

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

  // 字段定义：section -> 字段列表（type: text/number/bool/enum）
  var FIELDS = {
    llm: [
      { key: "enabled", type: "bool", label: "启用模型", hint: "关闭后仅规则抽取" },
      { key: "provider", type: "enum", label: "接入方式",
        hint: "ollama=本机；openai=任意 OpenAI 兼容接口",
        options: [["ollama", "Ollama（本机）"], ["openai", "OpenAI 兼容 API"]] },
      { key: "base_url", type: "text", label: "服务地址",
        hint: "如 http://127.0.0.1:11434 或 https://api.deepseek.com/v1" },
      { key: "model", type: "text", label: "模型名", hint: "如 qwen2.5:3b-instruct、deepseek-chat" },
      { key: "api_key_env", type: "text", label: "API Key 环境变量", hint: "推荐用环境变量，不把 key 写进配置" },
      { key: "timeout_seconds", type: "number", label: "超时（秒）" },
      { key: "max_tokens", type: "number", label: "最大输出 tokens" },
      { key: "json_mode", type: "bool", label: "JSON 模式", hint: "服务不支持时自动降级" },
      { key: "max_repair_rounds", type: "number", label: "校验回喂轮数", hint: "0 = 不修复" },
    ],
    review: [
      { key: "enabled", type: "bool", label: "启用第三方复核" },
      { key: "provider", type: "enum", label: "复核接入方式",
        options: [["ollama", "Ollama（本机）"], ["openai", "OpenAI 兼容 API"]] },
      { key: "base_url", type: "text", label: "复核服务地址" },
      { key: "model", type: "text", label: "复核模型名" },
      { key: "api_key_env", type: "text", label: "复核 API Key 环境变量" },
    ],
    extraction: [
      { key: "min_confidence", type: "number", label: "最低置信度" },
      { key: "require_tender_document", type: "bool", label: "强制文档类型校验",
        hint: "关闭后非招标文件也会抽取" },
      { key: "document_type_threshold", type: "number", label: "文档类型阈值",
        hint: "默认 4.0，误判时可调低" },
    ],
    output: [
      { key: "dir", type: "text", label: "结果输出目录", hint: "留空用默认数据目录" },
    ],
  };

  function sectionValue(section) {
    return (state.settings || {})[section] || {};
  }

  function isOverridden(section, key) {
    var ov = (state.overrides || {})[section];
    return !!ov && Object.prototype.hasOwnProperty.call(ov, key);
  }

  // ---------------- 渲染表单 ----------------
  function renderField(section, def) {
    var value = sectionValue(section)[def.key];
    var cellId = "f-" + section + "-" + def.key;
    var label = "<label for=\"" + cellId + "\">" + esc(def.label) +
      (isOverridden(section, def.key) ? "<span class=\"ovv\">已覆盖</span>" : "") +
      (def.hint ? "<small>" + esc(def.hint) + "</small>" : "") + "</label>";
    var input;
    if (def.type === "bool") {
      input = "<input type=\"checkbox\" id=\"" + cellId + "\"" + (value ? " checked" : "") + ">";
    } else if (def.type === "enum") {
      input = "<select id=\"" + cellId + "\">" + def.options.map(function (opt) {
        return "<option value=\"" + esc(opt[0]) + "\"" + (value === opt[0] ? " selected" : "") + ">" +
          esc(opt[1]) + "</option>";
      }).join("") + "</select>";
    } else {
      input = "<input type=\"" + (def.type === "number" ? "number" : "text") +
        "\" id=\"" + cellId + "\" value=\"" + esc(value == null ? "" : value) + "\"";
      if (def.type === "number") {
        input += " step=\"any\"";
        if (/seconds|tokens|rounds|threshold/.test(def.key)) input += " min=\"0\"";
      }
      input += ">";
    }
    return label + "<div>" + input + "</div>";
  }

  function renderAll() {
    Object.keys(FIELDS).forEach(function (section) {
      $("form-" + section).innerHTML = FIELDS[section].map(function (def) {
        return renderField(section, def);
      }).join("");
    });
  }

  function collect() {
    var payload = {};
    Object.keys(FIELDS).forEach(function (section) {
      var patch = {};
      FIELDS[section].forEach(function (def) {
        var el = $("f-" + section + "-" + def.key);
        var value;
        if (def.type === "bool") value = el.checked;
        else if (def.type === "number") value = el.value === "" ? null : Number(el.value);
        else value = el.value;
        patch[def.key] = value;
      });
      payload[section] = patch;
    });
    return payload;
  }

  function loadSettings() {
    return api("/api/settings").then(function (data) {
      state.settings = data.settings || {};
      state.overrides = data.overrides || {};
      renderAll();
    });
  }

  function flashOk() {
    var el = $("ok");
    el.classList.add("show");
    setTimeout(function () { el.classList.remove("show"); }, 2500);
  }

  $("save").addEventListener("click", function () {
    api("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ settings: collect() }),
    }).then(function (data) {
      state.settings = data.settings || {};
      state.overrides = data.overrides || {};
      renderAll();
      flashOk();
      showError("");
    }).catch(function (err) { showError(err.message || String(err)); });
  });

  $("reset").addEventListener("click", function () {
    if (!window.confirm("清除全部覆盖值，恢复出厂默认设置？")) return;
    api("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reset: true }),
    }).then(function () {
      return loadSettings();
    }).then(function () {
      flashOk();
      showError("");
    }).catch(function (err) { showError(err.message || String(err)); });
  });

  loadSettings().catch(function (err) { showError(err.message || String(err)); });
})();
