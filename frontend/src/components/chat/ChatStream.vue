<script>
import { EVIDENCE_NOTICE, MATCH_LABELS } from '../../services/chat_policy.js';

// 会话流组件只负责按卡片类型渲染接口返回的数据；所有动作通过 act 事件
// 交回页面 composable 执行，组件不直接调用接口。文本一律插值，不编译。
// 判定来源与证据提示来自固定常量，不由模型生成。
export default {
  props: {
    messages: { type: Array, required: true },
    busy: { type: Boolean, default: false },
    working: { type: Boolean, default: false },
  },
  emits: ['act'],
  setup() { return { matchLabels: MATCH_LABELS, evidenceNotice: EVIDENCE_NOTICE }; },
};
</script>

<template>
  <section class="chat-stream" aria-live="polite" aria-label="对话记录">
    <div v-for="message in messages" :key="message.id" class="chat-msg" :class="message.role">
      <span class="chat-via" v-if="message.via && matchLabels[message.via]">{{ matchLabels[message.via] }}</span>
      <div class="chat-bubble" v-if="message.text">{{ message.text }}</div>
      <div class="chat-card" v-if="message.card">
        <!-- 进行中：真实阶段与百分比，完成 / 跳过 / 失败时卡片会被替换 -->
        <template v-if="message.card.type === 'progress'">
          <div class="chat-card-head"><b>{{ message.card.job?.stage || '排队中' }}</b><span class="hintline">{{ message.card.job?.elapsed || '0.0' }}s · {{ message.card.job?.progress || 0 }}%</span></div>
          <div class="bar determinate" role="progressbar" :aria-valuenow="message.card.job?.progress || 0" aria-valuemin="0" aria-valuemax="100"><i :style="{ width: (message.card.job?.progress || 0) + '%' }"></i></div>
        </template>
        <!-- 结果摘要：数字与下载链接全部来自任务接口 -->
        <template v-else-if="message.card.type === 'summary'">
          <div class="chat-card-head"><b>{{ message.card.job?.filename || message.card.job?.id }}</b><span class="tag" :class="message.card.job?.status === 'done' ? 't-ok' : 't-mute'">{{ message.card.job?.status === 'done' ? '已完成' : message.card.job?.status }}</span></div>
          <div class="chat-stats">
            <span>字段有值 <b>{{ message.card.job?.summary?.fields_with_value || 0 }}</b></span>
            <span>模板覆盖 <b>{{ message.card.job?.summary?.templates_filled || 0 }}/{{ message.card.job?.summary?.templates_total || 0 }}</b></span>
            <span>高风险复核 <b>{{ message.card.job?.summary?.high || 0 }}</b></span>
            <span>用时 <b>{{ message.card.job?.elapsed || 0 }}s</b></span>
          </div>
          <div class="chat-ops">
            <a class="btn small" v-if="message.card.job?.downloads?.json" :href="message.card.job.downloads.json">结果 JSON</a>
            <a class="btn small" v-if="message.card.job?.downloads?.md" :href="message.card.job.downloads.md">审核报告</a>
            <a class="btn small" v-if="message.card.job?.downloads?.draft" :href="message.card.job.downloads.draft">平台草稿</a>
            <button class="btn small" @click="$emit('act', 'missing')">缺失与复核</button>
            <button class="btn small" :disabled="working" @click="$emit('act', 'generate', message.card.job)">生成招标文件</button>
            <a class="btn small primary" :href="'/?job=' + encodeURIComponent(message.card.job?.id || '')">完整字段与证据</a>
            <a class="btn small primary" :href="'/fill.html?job=' + encodeURIComponent(message.card.job?.id || '')">去填写</a>
          </div>
          <div class="chat-evidence">{{ evidenceNotice }}</div>
        </template>
        <!-- 待复核清单：来自 /result 的 review 数组，只展示不修改 -->
        <template v-else-if="message.card.type === 'review'">
          <div class="chat-review-item" v-for="(item, index) in message.card.items" :key="index">
            <div class="chat-review-head"><span class="tag" :class="{ 't-bad': item.priority === 'high', 't-warn': item.priority === 'medium', 't-ok': item.priority === 'low' }">{{ item.priority }}风险</span><b>{{ item.label || item.key }}</b></div>
            <div class="chat-review-issue">{{ item.issue }}</div>
          </div>
          <div class="chat-ops"><a class="btn small primary" :href="'/?job=' + encodeURIComponent(message.card.job?.id || '')">到详情页逐条复核</a></div>
          <div class="chat-evidence">{{ evidenceNotice }}</div>
        </template>
        <!-- 解析记录列表：选择后作为后续任务类指令的操作对象 -->
        <template v-else-if="message.card.type === 'jobs'">
          <div class="chat-job-row" v-for="item in message.card.jobs" :key="item.id">
            <div class="chat-job-info"><b>{{ item.filename }}</b><span class="hintline">{{ item.status }} · 字段有值 {{ item.summary?.fields_with_value || 0 }}</span></div>
            <div class="chat-ops">
              <button class="btn small" v-if="['done', 'skipped', 'error'].includes(item.status)" @click="$emit('act', 'pick-job', item)">选择</button>
              <a class="btn small" :href="'/?job=' + encodeURIComponent(item.id)">详情</a>
            </div>
          </div>
        </template>
        <!-- 下载入口：沿用任务接口给出的附件地址 -->
        <template v-else-if="message.card.type === 'downloads'">
          <div class="chat-ops">
            <a class="btn small" v-if="message.card.job?.downloads?.json" :href="message.card.job.downloads.json">结果 JSON</a>
            <a class="btn small" v-if="message.card.job?.downloads?.md" :href="message.card.job.downloads.md">审核报告</a>
            <a class="btn small" v-if="message.card.job?.downloads?.draft" :href="message.card.job.downloads.draft">平台草稿</a>
            <span class="hintline" v-if="!message.card.job?.downloads?.json && !message.card.job?.downloads?.md && !message.card.job?.downloads?.draft">该任务暂无可下载文件</span>
          </div>
        </template>
        <!-- 删除需二次确认，确认权始终在用户手里 -->
        <template v-else-if="message.card.type === 'confirm-delete'">
          <div class="chat-ops">
            <button class="btn small delete-soft" :disabled="working" @click="$emit('act', 'delete-yes', message.card.job)">确认删除</button>
            <button class="btn small" @click="$emit('act', 'pick-job', message.card.job)">先保留</button>
          </div>
        </template>
      </div>
    </div>
    <div class="chat-typing hintline" v-if="busy || working">{{ busy ? '正在处理…' : '正在执行任务…' }}</div>
  </section>
</template>
