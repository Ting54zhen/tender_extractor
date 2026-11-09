<script>
// customForm 是工作台创建的独立编辑草稿，输入只修改草稿；保存事件交由上层
// 校验后写入完整字段状态与本地存储，取消不会覆盖已经提交的字段。
export default {
  props: ['customForm', 'placeholderOptions'],
  emits: ['save', 'close'],
};
</script>

<template>
  <dialog id="dlgCustom">
    <div class="dlg-body">
      <h3 id="cfTitle">{{ customForm.key ? '编辑自定义字段' : '添加自定义字段' }}</h3><label>字段名（必填）</label><input id="cfLabel"
        maxlength="60" v-model="customForm.label"><label>字段值</label><textarea id="cfValue" rows="3"
        v-model="customForm.value"></textarea><label>类型</label><select id="cfType" v-model="customForm.type">
        <option value="text">单行文本</option>
        <option value="long_text">多行文本</option>
        <option value="money">金额</option>
        <option value="date">日期</option>
        <option value="select">下拉选择</option>
      </select><label>写入位置</label>
      <div class="cf-radios"><label><input type="radio" name="cfTarget" value="append"
            v-model="customForm.target">追加到文末「补充条款」表格</label><label><input type="radio" name="cfTarget"
            value="placeholder" v-model="customForm.target">写入模板已有占位符</label></div>
      <div class="cf-row" id="cfPlaceholderRow" :hidden="customForm.target !== 'placeholder'">
        <label>选择占位符</label><select id="cfPlaceholder" v-model="customForm.placeholder">
          <option value="">请选择占位符</option>
          <option v-for="name in placeholderOptions" :key="name" :value="name">{{ name }}</option>
        </select>
        <div class="cf-hint">仅列出当前模板尚未绑定的占位符。</div>
      </div>
      <div class="cf-row" id="cfOptionsRow" :hidden="customForm.type !== 'select'"><label>候选值（每行一个）</label><textarea
          id="cfOptions" rows="2" v-model="customForm.options"></textarea></div>
      <div class="cf-hint">自定义字段是人工输入，不会写入原文证据，也不会被标成「已抽取」。</div>
      <div class="dlg-foot"><button class="btn" id="cfCancel" @click="$emit('close')">取消</button><button
          class="btn primary" id="cfSave" @click="$emit('save')">保存</button></div>
    </div>
  </dialog>
</template>
