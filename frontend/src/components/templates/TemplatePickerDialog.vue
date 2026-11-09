<script>
// 页面负责切换模板和关闭弹窗；组件只渲染可选项，切换期间保持按钮禁用。
export default {
  props: ['templates', 'templateId', 'scopeLoading'],
  emits: ['pick', 'close'],
};
</script>

<template>
  <dialog id="dlgTemplate">
    <div class="dlg-body">
      <h3>选择招标文件模板</h3>
      <p>切换模板不会丢失已经填写的值。若占位符未匹配，成稿会显示「待补充」。</p>
      <div class="tpl-pick" id="tplPick">
        <div class="empty" v-if="!templates.length">模板库为空，请先到「模板库」上传一套。</div>
        <div class="item" v-for="template in templates" :key="template.id"><span class="nm">{{ template.name
        }}</span><span class="meta">{{ template.builtin ? '内置' : '自有' }} · 未匹配 {{ (template.unmapped || []).length
            }}</span>
          <div class="spacer"></div><button class="btn small" :disabled="scopeLoading" :data-pick="template.id"
            @click="$emit('pick', template.id)">{{ template.id === templateId ? '当前使用' : '使用' }}</button>
        </div>
      </div>
      <div class="dlg-foot"><button class="btn" id="tpClose" @click="$emit('close')">关闭</button></div>
    </div>
  </dialog>
</template>
