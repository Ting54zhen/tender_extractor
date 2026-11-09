<script>
import { computed } from 'vue';
import { statusOf } from '../../utils/field_status.js';

/* 字段卡片只负责展示与发出编辑意图；真实值留在工作台的全量状态中。
 * 切换分组卸载卡片不会丢值，证据、置信度与原始抽取值也不会被人工操作覆盖。
 */
const readOnly = {
  procurement_target_list: "清单为只读参考；写进招标文件请到「采购标的」字段点「从标的清单导入」。",
  star_clauses_summary: "打★的条款不满足即废标，打▲的为重要项；投标前需逐条自查。",
  qualification_requirements: "投标人需逐条响应并提供证明材料，缺项可能导致资格审查不通过。"
};
export default {
  props: ["item", "value", "mode", "suggestion", "targetList", "flash"], emits: ["change", "edit-custom", "delete-custom"],
  setup(props, { emit }) {
    const status = computed(() => statusOf(props.item));
    const custom = computed(() => props.item.custom ? props.item.cf : null);
    const inputKind = computed(() => custom.value?.type || (props.item.key === "procurement_target" || String(props.item.value || "").length > 44 ? "long_text" : "text"));
    const tagText = computed(() => ({ missing: "AI 未抽取 · 请手动填写", conflict: "存在冲突 · 请核对", invalid: "校验未通过", low: "低置信 " + Math.round((props.item.confidence || 0) * 100) + "%", ok: "已抽取 " + Math.round((props.item.confidence || 0) * 100) + "%" })[status.value]);
    const modeText = computed(() => ({ edited: "已修改", accepted: "已采纳", cleared: "已清空", history: "已采用历史值", suggested: "已采纳 AI 建议" })[props.mode] || "");
    function change(value, mode = "edited") { emit("change", { value, mode }); }
    function importList() {
      const names = String(props.targetList || "").split("\n").map(line => (line.replace(/^\s*\d+[.、)）]\s*/, "").split(/[（(：:]/, 1)[0] || "").trim()).filter(Boolean);
      change([...new Set(names)].join("\n"));
    }
    return { status, custom, inputKind, tagText, modeText, readOnly, change, importList };
  }
};
</script>

<template>
  <div class="field" :class="[status, { custom: item.custom, accepted: mode === 'accepted', flash: flash }]"
    :data-key="item.key" :data-template="item.na ? null : item.template || ''" :data-na="item.na ? '1' : null">
    <div class="field-head"><span class="label">{{ item.label }}</span>
      <span class="cf-tag" v-if="item.custom">{{ custom.scope ? '类型专属' : '自定义' }}</span>
      <span class="tpl" v-if="item.na">不适用</span><span class="tpl" v-else-if="item.custom">{{ custom.target ===
        'placeholder' ? '占位符：' + custom.placeholder : '追加到补充条款' }}</span>
      <span class="tpl" v-else-if="item.template && item.template !== item.label">{{ item.template }}</span>
      <span class="spacer"></span><span class="tag t-mute" v-if="item.na">{{ item.na_tag || '不适用' }}</span><span
        class="tag work" v-else-if="item.custom">人工填写</span><span class="tag t-ok"
        v-else-if="readOnly[item.key]">自动识别</span><span class="tag" :class="status" v-else>{{ tagText }}</span>
    </div>
    <template v-if="item.na"><input type="text" data-input value="" disabled>
      <div class="empty-hint">{{ item.na_reason || '当前项目类型下无需填写本字段。' }}</div>
    </template>
    <template v-else-if="readOnly[item.key]">
      <ol class="item-list">
        <li v-for="(line, i) in String(value).split('\n').filter(l => l.trim())" :key="i">{{ line }}</li>
      </ol>
      <div class="empty-hint">{{ readOnly[item.key] }}</div>
    </template>
    <template v-else>
      <textarea v-if="inputKind === 'long_text'" data-input :rows="item.key === 'procurement_target' ? 4 : 3"
        :value="value" @input="change($event.target.value)"></textarea>
      <select v-else-if="inputKind === 'select' && custom.options?.length" data-input :value="value"
        @change="change($event.target.value)">
        <option value="">请选择</option>
        <option v-for="option in custom.options" :key="option" :value="option">{{ option }}</option>
      </select>
      <input v-else :type="inputKind === 'date' ? 'date' : 'text'" data-input :value="value"
        @input="change($event.target.value)">
      <div class="empty-hint" v-if="item.key === 'procurement_target'">可多项：每行填一个标的。</div>
      <div class="hist-hint" v-if="item.key === 'procurement_target' && targetList">已识别采购标的，可从清单一键导入<button
          class="btn small" data-act="import-list" @click="importList">从标的清单导入</button></div>
      <div class="empty-hint" v-if="item.errors?.length || item.warnings?.length">{{ (item.errors?.length ? item.errors
        : item.warnings).join('；') }}</div>
      <div class="empty-hint" v-else-if="!item.custom && status === 'missing'">AI 没有从需求报告里找到这项，请手动填写或留空。</div>
      <div class="empty-hint" v-if="custom?.hint">{{ custom.hint }}</div>
      <div class="hist-hint" v-if="item.history && !value">上次填写：<b>{{ item.history.value }}</b><span class="src">{{
        String(item.history.updated_at || '').slice(0, 10) }}</span><button class="btn small" data-act="use-hist"
          @click="change(String(item.history.value), 'history')">采用</button></div>
      <div class="sugg-hint" v-if="suggestion && !value">AI 建议（需人工确认）：<b>{{ suggestion.value }}</b><span class="src">{{
        suggestion.reason }}</span><button class="btn small" data-act="use-sugg"
          @click="change(String(suggestion.value), 'suggested')">采纳建议</button></div>
      <details class="evidence" v-if="!item.custom && item.evidence?.length">
        <summary>查看原文证据（{{ item.evidence.length }} 条）</summary><template v-for="(evidence, i) in item.evidence"
          :key="i">
          <blockquote>{{ evidence.quote }}</blockquote>
          <div class="sect">{{ evidence.block_id || '' }} {{ evidence.section ? '来自：' + evidence.section : '' }}</div>
        </template>
      </details>
      <div class="field-actions"><template v-if="item.custom"><button v-if="!custom.scope" class="btn small ghost"
            data-act="cf-edit" @click="$emit('edit-custom')">编辑</button><button v-if="!custom.scope"
            class="btn small ghost danger" data-act="cf-del" @click="$emit('delete-custom')">删除</button></template>
        <template v-else><button class="btn small" data-act="accept"
            :disabled="item.value == null || String(item.value).trim() === ''"
            @click="change(String(item.value), 'accepted')">采纳 AI 值</button><button class="btn small ghost"
            data-act="clear" @click="change('', 'cleared')">清空</button></template><span class="state"
          :class="{ changed: modeText }" data-state>{{ modeText }}</span>
      </div>
    </template>
  </div>
</template>
