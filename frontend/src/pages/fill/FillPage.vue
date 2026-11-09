<script>
import AppShell from '../../components/layout/AppShell.vue';
import { usePage } from './use_fill_page.js';
import FillPreviewPane from '../../components/preview/FillPreviewPane.vue';
import TemplatePickerDialog from '../../components/templates/TemplatePickerDialog.vue';
import CustomFieldDialog from '../../components/fields/CustomFieldDialog.vue';
import FieldEditor from '../../components/fields/FieldEditor.vue';

// 仅组合本页状态与显式组件；所有模板在构建时编译，不编译接口或文档文本。
export default { components: { AppShell, FieldEditor, FillPreviewPane, TemplatePickerDialog, CustomFieldDialog }, setup: usePage };
</script>

<template>
  <div class="shell">
    <AppShell></AppShell>
    <main class="main">
      <div class="main-inner wide">
        <div class="fill-head">
          <div class="fill-head-main">
            <h1>填写工作台</h1>
            <p id="fileInfo">{{ jobInfo ? jobInfo.filename + ' · 状态：' + (jobInfo.stage || jobInfo.status) : '正在加载任务…' }}
            </p>
          </div>
          <div class="fill-head-actions"><button class="btn ghost" id="btnAcceptAll" :disabled="!ready"
              @click="acceptAll">全部采纳 AI 值</button><button class="btn" id="btnPreview" :disabled="!ready"
              @click="previewOpen = !previewOpen">{{ previewOpen ? '关闭预览' : '预览' }}</button><button class="btn primary"
              id="btnGenerate" :disabled="!ready || generating" @click="generate">{{ generating ? '生成中…' : '生成招标文件'
              }}</button></div>
        </div>
        <div class="progress">
          <div class="bar"><i id="barFill" :style="{ width: progress.percent + '%' }"></i></div>
          <div class="txt" id="progressText">已就绪 {{ progress.filled }} / {{ progress.total }}（{{ progress.percent }}%）
          </div>
        </div>
        <div class="scope-bar" id="scopeBar">
          <div class="scope-item" v-for="axis in axes" :key="axis.key"><label :for="axis.id">{{ axis.label
              }}</label><select :id="axis.id" v-model="scopeState[axis.key]" :disabled="loading" @change="changeScope">
              <option value="">请选择</option>
              <option v-for="option in scopeAxes[axis.key] || []" :key="option.code" :value="option.code">{{
                option.label }}</option>
            </select></div>
          <div class="scope-summary" id="scopeSummary">{{ scopeSummary }}</div>
          <div class="scope-actions" id="scopeActions"><button
              v-if="scopeData?.template_hint?.matched && scopeData.template_hint.id !== templateId" class="btn small"
              @click="pickTemplate(scopeData.template_hint.id)">使用「{{ scopeData.template_hint.name }}」</button></div>
        </div>
        <div id="scopeNotice" class="notice warn" v-show="scopeNotice">{{ scopeNotice }}</div>
        <div id="scopeGate" class="notice scope-gate" :hidden="scopeComplete">请先选择「采购方式 / 业务类型 / 招标组织形式」，字段区会按所选类型加载。
        </div>
        <div class="tpl-line" id="tplLine" :hidden="!jobInfo"><span>当前模板：<b id="tplName">{{ currentTemplate?.name ||
              '默认模板' }}</b></span><span id="tplMeta">{{ unmapped.length ? '未匹配占位符 ' + unmapped.length + ' 个' : ''
            }}</span>
          <div class="spacer"></div><button class="btn small ghost" id="btnPickTemplate"
            :disabled="loading || scopeLoading" @click="dialog('dlgTemplate')">切换模板</button>
        </div>
        <div id="notice" class="notice" :class="notice.kind" v-show="notice.text">{{ notice.text }}</div>
        <div id="loading" class="notice" :class="{ hidden: !loading && !scopeLoading }">{{ scopeLoading ? '正在按类型加载字段…' :
          '正在加载抽取结果…' }}</div>
        <div id="app" class="app" :class="{ 'with-preview': previewOpen, hidden: !ready }">
          <aside class="nav" id="nav">
            <h2>字段分组</h2><a v-for="group in groups" :key="group.key" class="nav-item"
              :class="{ active: group.key === activeGroup }" :href="'#g-' + group.key" :data-g="group.key"
              @click.prevent="selectGroup(group.key)"><span class="nm">{{ group.label }}</span><span class="count">{{
                groupStats(group).filled }}/{{ groupStats(group).total }}<i class="warn-dot"
                  v-if="groupStats(group).missing">{{ groupStats(group).missing }}</i></span></a>
          </aside>
          <div class="fill-body">
            <div class="toolbar">
              <div class="seg" id="filterSeg"><button v-for="option in filters" :key="option[0]" :data-f="option[0]"
                  :class="{ on: filter === option[0] }" @click="filter = option[0]">{{ option[1] }}</button></div>
              <div class="spacer"></div><button class="btn small" id="btnAddCustom" @click="openCustom('')">＋
                自定义字段</button><button class="btn small" id="btnSuggest" :disabled="suggesting" @click="suggest">{{
                  suggesting ? '起草中…' : 'AI 起草缺失字段' }}</button><label class="chip">缺失字段<select id="missingMode"
                  v-model="missingMode">
                  <option value="mark">标记「待补充」</option>
                  <option value="empty">留空</option>
                </select></label>
            </div>
            <div id="groups">
              <section class="group" v-if="selectedGroup" :id="'g-' + selectedGroup.key">
                <div class="group-head">
                  <h3>{{ selectedGroup.label }}</h3><span class="group-stat">本组 {{ groupStats(selectedGroup).total }}
                    个字段 · 已填 {{ groupStats(selectedGroup).filled }} <b v-if="groupStats(selectedGroup).missing">· 待补 {{
                      groupStats(selectedGroup).missing }}</b></span>
                </div>
                <div class="notice" v-if="!visibleItems.length">当前筛选条件下本组没有字段。</div>
                <FieldEditor v-for="item in visibleItems" :key="item.key" :item="item" :value="valueOf(item)"
                  :mode="edits[item.key]?.mode || ''" :suggestion="suggestions[item.key]" :target-list="targetList"
                  :flash="flashKey === item.key" @change="setField(item, $event.value, $event.mode)"
                  @edit-custom="openCustom(item.key)" @delete-custom="askDelete(item.key)"></FieldEditor>
              </section>
              <div class="notice" v-else>没有可显示的字段分组。</div>
              <div class="pager"><button class="btn" v-if="previousGroup" @click="selectGroup(previousGroup.key)">← {{
                  previousGroup.label }}</button><span v-else></span><button class="btn primary" v-if="nextGroup"
                  @click="selectGroup(nextGroup.key)">{{ nextGroup.label }} →</button><span v-else></span></div>
            </div>
          </div>
          <FillPreviewPane :preview-open="previewOpen" :preview="preview" :preview-loading="previewLoading"
            :preview-error="previewError" :preview-sections="previewSections" v-model:only-missing="onlyMissing"
            :generating="generating" :ready="ready" @close="previewOpen = false" @locate="locateField"
            @refresh="refreshPreview" @generate="generate" />
        </div>
      </div>
    </main>
  </div>
  <TemplatePickerDialog :templates="templates" :template-id="templateId" :scope-loading="scopeLoading"
    @pick="pickTemplate" @close="dialog('dlgTemplate', false)" />
  <CustomFieldDialog :custom-form="customForm" :placeholder-options="placeholderOptions" @save="saveCustom"
    @close="dialog('dlgCustom', false)" />
  <dialog id="dlgConfirm">
    <div class="dlg-body">
      <h3>删除自定义字段</h3>
      <p id="confirmText">确定删除「{{ deleting?.label }}」吗？删除后生成的招标文件里不再包含这项。</p>
      <div class="dlg-foot"><button class="btn" @click="dialog('dlgConfirm', false)">取消</button><button
          class="btn primary" id="confirmOk" @click="deleteCustom">删除</button></div>
    </div>
  </dialog>
  <div class="toast" v-if="toastText">{{ toastText }}</div>

</template>
