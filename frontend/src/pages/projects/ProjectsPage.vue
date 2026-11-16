<script>
import AppShell from '../../components/layout/AppShell.vue';
import DeleteConfirmDialog from '../../components/layout/DeleteConfirmDialog.vue';
import { usePage } from './use_projects_page.js';

// 仅组合本页状态与显式组件；所有模板在构建时编译，不编译接口或文档文本。
export default { components: { AppShell, DeleteConfirmDialog }, setup: usePage };
</script>

<template>
  <div class="shell">
    <AppShell></AppShell>
    <main class="main">
      <div class="main-inner projects-page">
        <div class="page-head">
          <h1>项目工作台</h1>
          <p>按采购项目归档任务；归档不删数据，随时恢复</p>
        </div>
        <div class="alert error" id="error" v-show="error" style="margin-bottom:14px">{{ error }}</div>
        <section class="card plain" id="list-view" v-show="!detail">
          <div class="toolbar">
            <div class="seg" id="status-seg">
              <button v-for="option in filters" :key="option[0]" :data-status="option[0]"
                :class="{ active: status === option[0] }" @click="selectStatus(option[0])">{{ option[1] }}</button>
            </div>
            <div class="spacer"></div><button class="btn primary" id="new-project" @click="openNew">＋ 新建项目</button>
          </div>
          <div class="grid" id="grid">
            <div class="empty" v-if="!projects.length">还没有项目——点右上角「新建项目」开始</div>
            <div v-for="project in projects" :key="project.id" class="pcard"
              :class="{ archived: project.status === 'archived' }" :data-open="project.id"
              @click="openDetail(project.id)">
              <div class="name">{{ project.name }}</div><span v-if="project.status === 'archived'"
                class="badge archived">已归档</span>
              <p v-if="project.note" class="note">{{ project.note }}</p>
              <div class="meta"><span>任务 {{ project.job_count }} 个</span><span>更新 {{ fmtTime(project.updated_at)
                  }}</span></div>
              <div class="ops" @click.stop><button class="btn small" @click="openDetail(project.id)">打开</button>
                <button class="btn small"
                  @click="action(project, project.status === 'active' ? 'archive' : 'restore')">{{ project.status ===
                    'active' ? '归档' : '恢复' }}</button>
                <button class="btn small" @click="openRename(project)">重命名</button><button class="btn small danger"
                  @click="action(project, 'delete')">删除</button>
              </div>
            </div>
          </div>
        </section>
        <section class="card plain" id="detail-view" v-if="detail">
          <div class="toolbar"><button class="btn small primary" id="back" @click="back">← 返回列表</button>
            <div class="spacer"></div><b id="detail-title">{{ detail.name }}（任务 {{ (detail.jobs || []).length }} 个）</b>
          </div>
          <div id="detail-jobs">
            <div class="empty" v-if="!detail.jobs.length">该项目还没有任务；到「需求解析」页上传文件时选择本项目即可</div>
            <template v-else>
              <div class="toolbar"><span class="hintline">完成后可整包导出：结果 JSON + 审核报告 + 招标文件 docx</span>
                <div class="spacer"></div>
                <button class="btn small primary" id="export-zip" :disabled="!doneCount || busy" @click="exportZip">{{
                  busy ? '打包中…' : '导出项目文件包（' + doneCount + ' 个任务）' }}</button>
              </div>
              <table class="tbl">
                <thead>
                  <tr>
                    <th>状态</th>
                    <th>文件</th>
                    <th>阶段</th>
                    <th>创建时间</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="job in detail.jobs" :key="job.id">
                    <td><span class="tag" :class="jobTag(job.status)[1]">{{ jobTag(job.status)[0] }}</span></td>
                    <td style="word-break:break-all">{{ job.filename }}</td>
                    <td>{{ job.stage }}</td>
                    <td>{{ fmtTime(job.created_at) }}</td>
                    <td><a class="btn small" :href="jobTarget(job)">查看</a></td>
                  </tr>
                </tbody>
              </table>
            </template>
          </div>
        </section>
        <footer>数据保存在本机 data/tender.db，不会上传到外部服务</footer>
      </div>
    </main>
  </div>
  <!-- 文案沿用旧确认框，只替换视觉和交互承载。 -->
  <DeleteConfirmDialog :open="!!pendingDelete" title="删除项目" :message="'删除项目「' + (pendingDelete?.name || '') + '」？项目下的任务与字段确认值将一并删除，不可恢复。'" :busy="busy" :error="deleteError" @cancel="pendingDelete = null" @confirm="confirmDelete" />
  <dialog id="dlg-new">
    <div class="dlg-body">
      <h3>新建项目</h3><label>项目名称（必填）</label><input id="np-name" v-model="draft.name"><label>备注（可选）</label><textarea
        id="np-note" v-model="draft.note"></textarea>
      <div class="dlg-foot"><button class="btn" @click="dialog('dlg-new', false)">取消</button><button class="btn primary"
          id="np-create" :disabled="busy" @click="create">创建</button></div>
    </div>
  </dialog>
  <dialog id="dlg-rename">
    <div class="dlg-body">
      <h3>重命名项目</h3><input id="rn-name" v-model="rename.name">
      <div class="dlg-foot"><button class="btn" @click="dialog('dlg-rename', false)">取消</button><button
          class="btn primary" id="rn-save" :disabled="busy" @click="saveRename">保存</button></div>
    </div>
  </dialog>

</template>
