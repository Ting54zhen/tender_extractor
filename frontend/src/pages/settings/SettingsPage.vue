<script>
import AppShell from '../../components/layout/AppShell.vue';
import { usePage } from './use_settings_page.js';

// 仅组合本页状态与显式组件；所有模板在构建时编译，不编译接口或文档文本。
export default { components: { AppShell }, setup: usePage };
</script>

<template>
  <div class="shell">
    <AppShell></AppShell>
    <main class="main">
      <div class="main-inner">
        <div class="page-head">
          <h1>设置</h1>
          <p>出厂默认来自 config/settings.json；此处保存的覆盖值存于本机数据库，立即生效</p>
        </div>
        <div class="alert error" id="error" v-show="error" style="margin-bottom:14px">{{ error }}</div>
        <section class="card"><template v-for="section in sections" :key="section.key">
            <h2 class="sec">{{ section.title }}</h2>
            <div class="form-grid" :id="'form-' + section.key">
              <template v-for="field in definitions[section.key]" :key="field.key">
                <label :for="inputId(section.key, field.key)">{{ field.label }}<span class="ovv"
                    v-if="isOverridden(section.key, field.key)">已覆盖</span><small v-if="field.hint">{{ field.hint
                    }}</small></label>
                <div v-if="settings[section.key]">
                  <input v-if="field.type === 'bool'" type="checkbox" :id="inputId(section.key, field.key)"
                    v-model="settings[section.key][field.key]">
                  <select v-else-if="field.type === 'enum'" :id="inputId(section.key, field.key)"
                    v-model="settings[section.key][field.key]">
                    <option v-for="option in field.options" :key="option[0]" :value="option[0]">{{ option[1] }}</option>
                  </select>
                  <input v-else :type="field.type === 'number' ? 'number' : 'text'"
                    :id="inputId(section.key, field.key)" :value="settings[section.key][field.key] ?? ''" step="any"
                    @input="setValue(section.key, field, $event.target.value)">
                </div>
              </template>
            </div>
          </template>
          <div class="actions"><button class="btn primary" id="save" :disabled="busy || !loaded" @click="save">{{ busy ?
            '保存中…' :
              '保存设置' }}</button><button class="btn" id="reset" :disabled="busy || !loaded"
              @click="reset">恢复出厂默认（清除覆盖）</button><span class="ok-msg" :class="{ show: saved }" id="ok">已保存并生效</span>
          </div>
        </section>
        <footer>覆盖值保存在本机 data/tender.db；角标「已覆盖」表示该项有本地覆盖值</footer>
      </div>
    </main>
  </div>

</template>
