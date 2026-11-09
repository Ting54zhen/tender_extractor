<script>
import AppShell from '../../components/layout/AppShell.vue';
import DeleteConfirmDialog from '../../components/layout/DeleteConfirmDialog.vue';
import ChatStream from '../../components/chat/ChatStream.vue';
import { usePage } from './use_index_page.js';

// 仅组合本页状态与显式组件；所有模板在构建时编译，不编译接口或文档文本。
export default { components: { AppShell, DeleteConfirmDialog, ChatStream }, setup: usePage };
</script>

<template>
  <div class="shell">
    <AppShell></AppShell>
    <main class="main">
      <div class="main-inner wide requirements-page" :class="'view-' + view">
        <!-- 列表沿用项目工作台的卡片与筛选结构，详情链接始终带任务 ID。 -->
        <template v-if="view === 'list'">
          <div class="page-head"><h1>需求解析</h1><p>管理采购需求与解析记录，查看原文证据，继续编制招标文件</p></div>
          <section class="card plain">
            <div class="toolbar">
              <div class="seg" id="requirement-status"><button v-for="option in filters" :key="option[0]" :data-filter="option[0]" :class="{ active: filter === option[0] }" @click="filter = option[0]">{{ option[1] }}</button></div>
              <div class="spacer"></div><input class="requirement-search" v-model="search" aria-label="搜索需求" placeholder="搜索需求报告">
              <button class="btn small" :disabled="listLoading" @click="loadJobs">刷新</button><a class="btn primary" href="/?view=new">＋ 新建解析</a>
            </div>
            <!-- 固定计数行占位，搜索与状态筛选只更新内容，不推动下方卡片。 -->
            <div class="search-summary" aria-live="polite"><span v-show="search.trim()">搜索结果 共{{ filteredJobs.length }}条</span></div>
            <div class="grid" id="requirements-grid">
              <div class="empty" v-if="!filteredJobs.length">{{ listLoading ? '正在加载需求…' : jobs.length ? '没有符合条件的需求' : '还没有解析记录，点击「新建解析」开始' }}</div>
              <a class="pcard requirement-card" v-for="item in filteredJobs" :key="item.id" :href="'/?job=' + encodeURIComponent(item.id)">
                <div class="requirement-card-head"><span class="tag" :class="jobLabels[item.status]?.[1] || 't-mute'">{{ jobLabels[item.status]?.[0] || item.status }}</span><span class="hintline">{{ item.use_llm ? '完整链路' : '仅规则' }}</span></div>
                <div class="name">{{ item.filename }}</div><p class="note">{{ projectName(item.project_id) }}</p>
                <div class="meta"><span>{{ item.stage || '排队中' }}</span><span v-if="['queued', 'running'].includes(item.status)">{{ item.progress || 0 }}%</span><span v-else>字段有值 {{ item.summary?.fields_with_value || 0 }} · 用时 {{ item.elapsed || 0 }}s</span></div>
                <div class="ops"><span>查看需求详情 →</span></div>
              </a>
            </div>
          </section>
        </template>
        <div v-if="view === 'detail'" class="detail-heading"><a class="btn small primary" id="requirement-back" href="/">← 返回需求列表</a><div class="spacer"></div><button v-if="job" class="btn small delete-soft" id="delete-requirement" :disabled="deleting || ['queued', 'running'].includes(detailJob?.status)" :title="['queued', 'running'].includes(detailJob?.status) ? '解析结束后可删除' : '删除需求解析记录'" @click="openDelete">删除需求</button><a class="btn small" href="/?view=new">＋ 新建解析</a></div>
        <div v-if="view !== 'list'" class="page-top"><span class="badge" :class="health.available ? 'ok' : 'bad'" id="health"
            :title="health.reason || ''"><span class="dot"></span><span id="health-text">{{ healthText
            }}</span></span><button class="btn small" id="refresh-health" @click="loadHealth">重新检测</button></div>
        <section class="hero" v-if="view === 'new'">
          <h1 class="hero-title">描述你的采购需求，<br>剩下的交给它</h1>
          <p class="hero-sub">上传采购需求报告，自动抽取结构化字段、生成可编辑的招标文件。<br>每一条结论都能回到原文证据。</p>
          <!-- 模板引用与 file 状态分开命名，避免 Vue 把 DOM 节点写入已选文件状态。 -->
          <div class="composer" :class="{ over: dragging }" @dragenter="enterDrag" @dragover="overDrag" @dragleave="leaveDrag" @drop="dropFile">
            <!-- 提示层不参与布局也不接收鼠标事件，拖动时保持输入区尺寸与事件边界稳定。 -->
            <div class="drop-upload-hint" v-show="dragging" role="status"><strong>松开即可上传需求报告</strong><span>支持 .docx / .pdf / .txt · 不超过 50MB</span></div>
            <!-- 文本与文件二选一；上传文件时保留文字草稿，移除文件即可继续输入。 -->
            <!-- 发送即对话：Enter 直接交给助手识别意图，Shift+Enter 换行。 -->
            <textarea ref="inputElement" id="requirement-text" class="requirement-input" rows="2" v-model="text" :disabled="!!file || chatBusy" aria-label="采购需求内容" placeholder="输入或粘贴采购需求，也可以直接对助手下指令" @keydown.enter.exact.prevent="chatSend()"></textarea>
            <!-- 上传提示与已选文件使用同一固定占位，文件名长度不会撑开页面。 -->
            <div class="upload-slot">
            <div class="dropzone" id="drop" v-show="!file" role="button" tabindex="0" @click="$refs.uploadInput.click()" @keydown.enter.prevent="$refs.uploadInput.click()" @keydown.space.prevent="$refs.uploadInput.click()">
              <div class="big">把需求报告拖到这里，或点击选择文件</div>
              <div class="hint">支持 .docx / .pdf / .txt · 单个文件不超过 50MB</div>
            </div>
            <div class="selected-upload" v-show="file">
            <div class="filebar" :class="{ show: file }" id="filebar"><span class="name" id="file-name">{{ file?.name
            }}</span><span class="size" id="file-size">{{ file ? fmtSize(file.size) : '' }}</span><button
                class="clear" id="file-clear" title="移除" @click="file = null">×</button></div>
            <div class="hintline upload-note">本次解析已上传的报告；移除文件后可解析输入的文字。</div>
            </div>
            </div>
            <input type="file" ref="uploadInput" id="file" accept=".docx,.pdf,.txt" hidden @click.stop @change="setFile($event.target.files[0]); $event.target.value = ''">
            <div class="composer-bar"><label class="chip">项目<select id="project" v-model="projectId"
                  @change="saveProject">
                  <option value="">（不归属项目）</option>
                  <option v-for="project in projects" :key="project.id" :value="project.id">{{ project.name }}（{{
                    project.job_count }}）</option>
                </select></label>
              <div class="seg-pill"><label><input type="radio" name="mode" value="full"
                    v-model="mode"><span>完整链路</span></label><label><input type="radio" name="mode" value="rules"
                    v-model="mode"><span>仅规则</span></label></div><label class="check"><input type="checkbox" id="force"
                  v-model="force">强制抽取</label><span class="hintline" id="run-hint">{{ chatBusy ?
                    '正在处理…' : canSubmit ? 'Enter 发送，或点击开始解析' : '输入需求或上传报告' }}</span><button class="btn primary start-extraction" id="run"
                :disabled="!canSubmit || chatBusy" @click="chatSend()" title="发送给助手" aria-label="开始解析">开始解析 <svg viewBox="0 0 24 24">
                  <path d="M5 12h14M13 6l6 6-6 6" />
                </svg></button>
            </div>
          </div>
          <!-- 对话消息流：任务进度、结果摘要与待复核清单内联在会话里。 -->
          <ChatStream v-if="chatMessages.length" :messages="chatMessages" :busy="chatBusy" :working="!!chatActionBusy" @act="(op, arg) => chatAct(op, arg)" />
          <!-- 能力提示改为发给助手演示；报告上传仍走上方拖放区。 -->
          <div class="sugg-tabs" id="sugg-tabs"><button v-for="tab in suggestionTabs" :key="tab[0]" class="sugg-tab"
              :class="{ on: suggestionTab === tab[0] }" @click="suggestionTab = tab[0]">{{ tab[1] }}</button></div>
          <div class="sugg-grid" id="sugg-grid"><a class="sugg-item" href="#" v-for="sugg in suggestionList" :key="sugg"
              @click.prevent="chatSend(sugg)"><svg viewBox="0 0 24 24">
                <path d="M4 4v7a4 4 0 0 0 4 4h11" />
                <path d="M15 11l4 4-4 4" />
              </svg><span>{{ sugg }}</span></a></div>
        </section>
        <!-- 顶部确定性进度保留至解析完成，避免结果页跳动；模型耗时不用于伪造百分比。 -->
        <section v-if="view === 'detail'" class="card status show requirement-progress" id="status" aria-live="polite">
          <div class="row">
            <div>
              <div style="font-weight:600" id="status-title">{{ result ? job.status === 'skipped' ? '文档已跳过' : '解析完成' : error ? '解析未完成' : progress.stage || detailJob?.stage || '正在载入需求…' }}</div>
              <div class="hintline" id="status-sub">{{ detailJob?.filename || '' }}</div>
            </div>
            <div class="progress-numbers"><strong id="status-percent">{{ percent }}%</strong><span class="hintline" id="status-elapsed">{{ progress.running ? progress.elapsed : detailJob?.elapsed || '0.0' }}s</span></div>
          </div>
          <div class="bar determinate" role="progressbar" aria-label="需求解析进度" aria-valuemin="0" aria-valuemax="100" :aria-valuenow="percent"><i :style="{ width: percent + '%' }"></i></div>
          <p class="hintline progress-caption">{{ result ? '解析结果已就绪，请结合原文证据人工复核。' : '进度按实际处理阶段更新，模型处理期间会停留在当前阶段。' }}</p>
          <a v-if="error" class="btn small" href="/?view=new">重新新建解析</a>
        </section>
        <section class="card" id="error-card" v-show="error">
          <div class="alert error">
            <div class="grow" id="error-text">{{ error }}</div>
          </div>
        </section>
        <section id="result" v-if="result && job">
          <div class="card">
            <div class="alert warn" id="skipped-banner" v-if="job.status === 'skipped' || job.summary?.skipped">
              <div class="grow" id="skipped-text">未执行抽取：{{ job.summary?.reject_reason || '该文件未被识别为招标/采购需求文件'
              }}。如确认文件类型无误，请新建解析并勾选「强制抽取」。</div><a class="btn small" id="retry-force"
                href="/?view=new">重新选择报告</a>
            </div>
            <div class="stats" id="stats">
              <div class="stat" v-for="item in stats" :key="item[0]">
                <div class="k">{{ item[0] }}</div>
                <div class="v">{{ item[1] }}</div>
              </div>
            </div>
            <div class="toolbar"><a class="btn small" id="dl-json" v-if="job.downloads?.json"
                :href="job.downloads.json">下载结果 JSON</a><a class="btn small" id="dl-md" v-if="job.downloads?.md"
                :href="job.downloads.md">下载审核报告</a><a class="btn small" id="dl-draft" v-if="job.downloads?.draft"
                :href="job.downloads.draft">下载平台草稿</a>
              <template v-if="job.status === 'done'"><button class="btn small" id="gen-bid" :disabled="!!actionBusy"
                  @click="runAction('bid')">{{ actionBusy === 'bid' ? '生成中…' : '生成投标书初稿' }}</button><button
                  class="btn small" id="gen-risk" :disabled="!!actionBusy" @click="runAction('analyze')">{{ actionBusy=== 'analyze' ? '分析中…' : '图谱风险分析' }}</button><button class="btn small" id="export-pdf"
                  :disabled="!!actionBusy" @click="exportPdf">导出 PDF</button><a class="btn small primary" id="go-fill"
                  :href="'/fill.html?job=' + job.id">去填写招标文件 →</a></template>
              <div class="spacer"></div><span class="hintline" id="result-meta">{{ resultMeta }}</span>
            </div>
            <div class="card" id="bid-result" v-if="bidResult" style="margin-top:14px">
              <div class="toolbar"><b>投标书初稿</b>
                <div class="spacer"></div><span class="hintline" id="bid-summary">资格响应 {{ bidResult.summary?.covered ||0 }}/{{
                    bidResult.summary?.qualifications || 0 }} · 技术方案 {{ bidResult.summary?.sections || 0 }} 节 · 缺口 {{
                    bidResult.summary?.gaps || 0 }} 项</span><button class="btn small" id="dl-bid"
                  @click="downloadBid">下载投标书
                  .md</button>
              </div>
              <div id="bid-gaps">
                <div class="alert warn" v-if="bidResult.gaps?.length">
                  <div class="grow">材料缺口 {{ bidResult.gaps.length }} 项：<span v-for="(gap, index) in bidResult.gaps"
                      :key="index">{{
                        gap.requirement }}→{{ gap.need }}；</span></div>
                </div>
              </div>
              <div class="md" id="bid-md" v-html="formatters.markdown(bidResult.markdown || '')"></div>
            </div>
            <div class="card" id="risk-result" v-if="riskResult" style="margin-top:14px">
              <div class="toolbar"><b>知识图谱风险分析</b>
                <div class="spacer"></div><span class="hintline" id="risk-meta">节点 {{ riskResult.graph?.nodes?.length ||0 }} 个 · 关系
                  {{ riskResult.graph?.edges?.length || 0 }} 条 {{ riskResult.meta?.error || '' }}</span><button
                  class="btn small" id="dl-turtle" @click="downloadGraph">下载图谱 .ttl</button>
              </div>
              <div id="graph-svg" style="overflow-x:auto;margin-bottom:12px"
                v-html="formatters.graph(riskResult.graph || {})">
              </div>
              <div class="md" id="risk-md" v-html="formatters.markdown(riskResult.knowledge_report || '')"></div>
            </div>
            <div class="tabs"><button v-for="tab in panes" :key="tab[0]" :data-pane="tab[0]"
                :class="{ active: pane === tab[0] }" @click="pane = tab[0]">{{ tab[1] }}</button></div>
            <div class="pane" :class="{ active: pane === 'fields' }" id="pane-fields">
              <table class="tbl fields">
                <thead>
                  <tr>
                    <th>字段</th>
                    <th>值</th>
                    <th>状态</th>
                    <th>置信度</th>
                    <th>来源</th>
                    <th>原文证据</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(field, key) in result.fields || {}" :key="key"
                    :class="{ dim: field.value == null || field.value === '' }">
                    <td>{{ field.label || key }}</td>
                    <td class="val">{{ field.value == null || field.value === '' ? '—' : field.value }}<div class="ev"
                        v-if="field.notes">{{ field.notes }}</div>
                    </td>
                    <td><span class="tag" :class="statusLabels[field.status]?.[1] || 't-mute'">{{
                      statusLabels[field.status]?.[0] ||
                      field.status }}</span></td>
                    <td>{{ field.confidence == null ? '—' : Math.round(field.confidence * 100) + '%' }}</td>
                    <td>{{ sourceLabels[field.source] || field.source || '—' }}</td>
                    <td>
                      <div class="ev" v-for="(evidence, i) in field.evidence || []" :key="i"><b>{{ evidence.block_id }}
                          {{
                            evidence.section }}</b> {{ evidence.quote }}</div><span class="ev"
                        v-if="!field.evidence?.length">无</span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div class="pane" :class="{ active: pane === 'review' }" id="pane-review">
              <div class="empty" v-if="!reviewItems.length">没有需要人工复核的项</div>
              <div class="review-item" :class="item.priority" v-for="(item, index) in reviewItems" :key="index">
                <div class="head"><span class="tag" :class="priorityLabels[item.priority]?.[1]">{{
                  priorityLabels[item.priority]?.[0] || item.priority }}风险</span><b>{{ item.label || item.key }}</b>
                </div>
                <div class="issue">{{ item.issue }}</div>
                <div class="cur">当前值：<span class="val">{{ item.value ?? '—' }}</span></div>
              </div>
            </div>
            <div class="pane" :class="{ active: pane === 'template' }" id="pane-template">
              <div class="kv"><template v-for="(value, name) in result.template_values || {}" :key="name">
                  <div class="k">{{ name }}</div>
                  <div class="val">{{ value }}</div>
                </template></div>
              <div class="alert warn" v-if="result.missing_templates?.length">模板缺失 {{ result.missing_templates.length }}
                项：{{
                  result.missing_templates.join('、') }}</div>
            </div>
            <div class="pane" :class="{ active: pane === 'report' }" id="pane-report">
              <div class="md" v-html="formatters.markdown(report)"></div>
            </div>
          </div>
        </section>
        <footer>文档默认在本机处理；选择外部模型接口时，以设置中的服务地址为准</footer>
      </div>
    </main>
  </div>

  <DeleteConfirmDialog :open="deleteOpen" title="删除需求" :message="'删除需求「' + (job?.filename || '') + '」？解析记录与字段确认值将一并删除，不可恢复。已保存的本机文档将保留。'" :busy="deleting" :error="deleteError" @cancel="deleteOpen = false" @confirm="deleteRequirement" />
</template>
