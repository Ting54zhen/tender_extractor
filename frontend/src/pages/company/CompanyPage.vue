<script>
import AppShell from '../../components/layout/AppShell.vue';
import { usePage } from './use_company_page.js';

// 仅组合本页状态与显式组件；所有模板在构建时编译，不编译接口或文档文本。
export default { components: { AppShell }, setup: usePage };
</script>

<template>
  <div class="shell">
    <AppShell></AppShell>
    <main class="main">
      <div class="main-inner company-page">
        <div class="page-head">
          <h1>企业档案</h1>
          <p>投标书生成的素材库：录入一次，后续每个项目自动复用</p>
        </div>
        <div class="alert error" id="error" v-show="error" style="margin-bottom:14px">{{ error }}</div>
        <section class="card" v-if="profile">
          <h2 class="sec">基本信息</h2>
          <!-- 显式声明文本类型与公共控件类，基础字段和动态行使用同一套外观。 -->
          <div class="form-grid" id="basic"><label v-for="pair in basic" :key="pair[0]">{{ pair[1] }}<input type="text" class="ui-input"
                :data-basic="pair[0]" v-model="profile.company[pair[0]]"></label></div>
          <template v-for="section in sections" :key="section.key">
            <h2 class="sec">{{ section.title }}</h2>
            <div class="row-list" :id="section.key">
              <div class="row-item" v-for="(item, index) in profile[section.key]" :key="rowKey(item)">
                <label v-for="field in lists[section.key].fields" :key="field[0]">{{ field[1] }}<input type="text" class="ui-input"
                    :data-f="field[0]" v-model="item[field[0]]"></label>
                <button class="btn small danger del" :data-del="index"
                  @click="profile[section.key].splice(index, 1)">删除</button>
              </div>
            </div>
            <button class="btn small" :id="lists[section.key].add" style="margin-top:8px" @click="addRow(section.key)">＋
              {{ section.add }}</button>
          </template>
          <h2 class="sec">证明材料索引</h2>
          <p class="hint">key 对应资格要求自动映射（如 business_license、sme），值写材料位置或说明。</p>
          <div class="kv-list" id="evidence">
            <div class="kv-item" v-for="(item, index) in evidence" :key="item.id"><input type="text" class="ui-input" data-k
                aria-label="材料标识" placeholder="材料标识，如 business_license" v-model="item.key"><input type="text" class="ui-input" data-v aria-label="材料位置或说明" placeholder="填写材料位置或说明" v-model="item.value"><button class="btn small danger"
                @click="evidence.splice(index, 1)">删除</button></div>
          </div>
          <button class="btn small" id="add-e" style="margin-top:8px" @click="addEvidence">＋ 添加材料</button>
          <div class="actions" style="margin-top:20px"><button class="btn primary" id="save" :disabled="busy"
              @click="save">保存档案</button><span class="ok-msg" :class="{ show: saved }" id="ok">已保存</span></div>
        </section>
        <footer>档案保存在本机 data/tender.db；投标书生成会自动引用这里的资质与业绩</footer>
      </div>
    </main>
  </div>

</template>
