<script>
import AppShell from '../../components/layout/AppShell.vue';
import { usePage } from './use_compare_page.js';

// 仅组合本页状态与显式组件；所有模板在构建时编译，不编译接口或文档文本。
export default { components: { AppShell }, setup: usePage };
</script>

<template>
  <div class="shell">
    <AppShell></AppShell>
    <main class="main">
      <div class="main-inner">
        <!-- 模型状态放在标题右侧，保留检测行为并与其他页面保持同一标题起点。 -->
        <div class="page-head with-actions">
          <div class="page-head-main">
            <h1>双文档符合性比对</h1>
            <p>招标要求 vs 投标响应 · 逐条判定 · 证据回链</p>
          </div>
          <div class="page-head-actions">
            <span class="badge" :class="health.available ? 'ok' : 'bad'" id="health-badge":title="health.reason || ''">
              <span class="dot"></span>
              <span id="health-text">{{ health.available ? '模型就绪 · ' + (health.model || '') : '模型不可用' }}</span>
            </span>
            <button class="btn small health-refresh" id="refresh-health" @click="loadHealth">重新检测</button>
          </div>
        </div>
        <section class="card">
          <div class="grid2">
            <div v-for="slot in slots" :key="slot.key">
              <div class="label">{{ slot.label }}<small>{{ slot.hint }}</small></div>
              <div class="dropzone" :id="'drop-' + slot.key"
                :class="{ filled: files[slot.key], over: dragging === slot.key }" @click="choose(slot.key)"
                @dragover.prevent="dragging = slot.key" @dragleave.prevent="dragging = ''"
                @drop.prevent="dragging = ''; setFile(slot.key, $event.dataTransfer.files[0])">
                <div class="big">点击或拖入{{ slot.label }}</div>
                <div class="hint" :id="'hint-' + slot.key">{{ files[slot.key] ? files[slot.key].name + ' · ' +
                  fmtSize(files[slot.key].size) : '支持 .docx / .pdf / .txt，最大 50MB' }}</div>
              </div><input type="file" :id="'file-' + slot.key" accept=".docx,.pdf,.txt" hidden
                @change="setFile(slot.key, $event.target.files[0]); $event.target.value = ''">
            </div>
          </div>
          <div class="options"><label><input type="checkbox" id="use-llm" v-model="useLlm">使用模型（关闭后无法完成比对）</label></div>
          <div class="actions"><button class="btn primary" id="run"
              :disabled="!files.tender || !files.bid || busy || progress.running" @click="submit">开始比对</button><span
              class="hintline" id="run-hint">{{ busy || progress.running ? '比对中，请稍候' : files.tender && files.bid ?
                '准备就绪' : '先选择招标文件与投标文件' }}</span></div>
          <div class="alert error" id="error" :hidden="!error">{{ error }}</div>
        </section>
        <section class="status card" id="status" :class="{ show: busy || progress.running }">
          <div class="row">
            <div class="spinner"></div>
            <div class="grow"><b id="status-title">{{ progress.stage || '正在上传…' }}</b>
              <div class="hintline" id="status-sub">{{ useLlm ? '抽取招标要求 → 抽取投标响应 → 逐条比对' : '已关闭模型，无法完成比对' }}</div>
            </div>
            <div class="hintline" id="status-elapsed">{{ progress.elapsed }}s</div>
          </div>
          <div class="bar"><i></i></div>
        </section>
        <section class="card" id="result" v-if="payload && job">
          <div class="stats" id="stats">
            <div class="stat" v-for="item in stats" :key="item[0]">
              <div class="k">{{ item[0] }}</div>
              <div class="v">{{ item[1] }}</div>
            </div>
          </div>
          <div class="alert warn" id="warn" :hidden="!!payload.results?.length">没有得到比对结论：可能是模型不可用、未抽取到资格要求，或投标文件未提供响应。
          </div>
          <div class="tbl-wrap">
            <table class="tbl" id="tbl">
              <thead>
                <tr>
                  <th>#</th>
                  <th>招标资格要求</th>
                  <th>判定</th>
                  <th>命中的投标响应</th>
                  <th>依据</th>
                </tr>
              </thead>
              <tbody id="tbody">
                <tr v-for="(item, index) in payload.results || []" :key="index">
                  <td class="idx">{{ index + 1 }}</td>
                  <td>{{ item.requirement }}<div class="cell-sub">{{ item.req_evidence }}</div>
                  </td>
                  <td><span class="verdict" :class="item.verdict || 'missing'">{{ verdictText[item.verdict] ||
                    item.verdict || '未响应' }}</span></td>
                  <td>{{ item.matched_response || '—' }}<div class="cell-sub">{{ item.resp_evidence }}</div>
                  </td>
                  <td>{{ item.note }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <details id="report-box">
            <summary>查看完整比对报告（Markdown）</summary>
            <pre id="report">{{ payload.report || '（无报告）' }}</pre>
          </details>
        </section>
      </div>
    </main>
  </div>

</template>
