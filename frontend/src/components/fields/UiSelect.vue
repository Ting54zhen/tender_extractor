<script>
import { computed, ref, watch, onMounted, onBeforeUnmount, nextTick } from 'vue';

/**
 * 采购类型使用与站内浮层一致的选项菜单，避免展开后退回系统默认外观。
 * 隐藏的原生 select 保留字段 ID、change 契约及自动化入口；交互按钮负责
 * 可见焦点、键盘导航与屏幕阅读器语义，选项值由父页面 v-model 管理。
 */
export default {
  props: { id: String, label: String, modelValue: { default: '' }, options: { type: Array, default: () => [] }, disabled: Boolean },
  emits: ['update:modelValue', 'change'],
  setup(props, { emit }) {
    const root = ref(null), trigger = ref(null), open = ref(false), active = ref(0);
    const choices = computed(() => [{ code: '', label: '请选择' }, ...props.options]);
    const selected = computed(() => choices.value.find(option => option.code === props.modelValue)?.label || '请选择');
    function choose(value) { emit('update:modelValue', value); emit('change'); open.value = false; trigger.value?.focus(); }
    function toggle() {
      if (props.disabled) return;
      open.value = !open.value;
      active.value = Math.max(0, choices.value.findIndex(option => option.code === props.modelValue));
    }
    function keydown(event) {
      if (props.disabled) return;
      if (event.key === 'Escape' || event.key === 'Tab') { open.value = false; return; }
      if (!['ArrowDown', 'ArrowUp', 'Home', 'End', 'Enter', ' '].includes(event.key)) return;
      event.preventDefault();
      if (!open.value) { toggle(); return; }
      if (event.key === 'Enter' || event.key === ' ') { choose(choices.value[active.value].code); return; }
      active.value = event.key === 'Home' ? 0 : event.key === 'End' ? choices.value.length - 1
        : (active.value + (event.key === 'ArrowUp' ? -1 : 1) + choices.value.length) % choices.value.length;
      // 长菜单随键盘选项滚动，焦点留在触发按钮，避免 Tab 被困在每个选项上。
      nextTick(() => root.value?.querySelector('[data-active="true"]')?.scrollIntoView({ block: 'nearest' }));
    }
    function outside(event) { if (!root.value?.contains(event.target)) open.value = false; }
    function focusout(event) { if (!root.value?.contains(event.relatedTarget)) open.value = false; }
    watch(() => props.disabled, value => { if (value) open.value = false; });
    onMounted(() => document.addEventListener('pointerdown', outside));
    onBeforeUnmount(() => document.removeEventListener('pointerdown', outside));
    return { root, trigger, open, active, choices, selected, choose, toggle, keydown, focusout };
  },
};
</script>

<template>
  <div class="ui-select-field" ref="root" @focusout="focusout" @keydown="keydown">
    <select :id="id" hidden aria-hidden="true" tabindex="-1" :disabled="disabled" :value="modelValue"
      @change="choose($event.target.value)"><option v-for="option in choices" :key="option.code" :value="option.code">{{ option.label }}</option></select>
    <button type="button" class="ui-select-trigger" ref="trigger" :id="id + '-trigger'" role="combobox"
      :aria-label="label" aria-haspopup="listbox" :aria-expanded="open" :aria-controls="id + '-options'"
      :aria-activedescendant="open ? id + '-option-' + active : undefined" :disabled="disabled" @click="toggle">
      <span :class="{ placeholder: !modelValue }">{{ selected }}</span><span class="ui-select-chevron" aria-hidden="true"></span>
    </button>
    <div v-if="open" class="ui-select-options" :id="id + '-options'" role="listbox" :aria-label="label">
      <div v-for="(option, index) in choices" :key="option.code" :id="id + '-option-' + index" role="option"
        :aria-selected="option.code === modelValue" :data-active="index === active" @mouseenter="active = index"
        @mousedown.prevent @click="choose(option.code)"><span>{{ option.label }}</span><span v-if="option.code === modelValue" aria-hidden="true">✓</span></div>
    </div>
  </div>
</template>
