<script>
import AppShell from '../../components/layout/AppShell.vue';
import { usePage } from './use_templates_page.js';
import DocumentPreview from '../../components/preview/DocumentPreview.vue';

// 仅组合本页状态与显式组件；所有模板在构建时编译，不编译接口或文档文本。
export default { components: { AppShell, DocumentPreview }, setup: usePage };
</script>

<template>
  <div class="shell">
    <AppShell></AppShell>
    <main class="main">
      <div class="main-inner wide">
        <div class="page-head">
          <h1>模板库</h1>
          <p>一套模板对应一种采购品目与采购方式的成稿版式；内置模板只读，自有模板可上传</p>
        </div>
        <div class="toolbar"><span class="chip">共 <b id="count">{{ templates.length }}</b> 套模板</span>
          <div class="spacer"></div>
          <button class="btn" id="btnRefresh" @click="load">刷新</button><button class="btn primary" id="btnUpload"
            @click="$refs.fileInput.click()">＋ 上传模板</button><input type="file" ref="fileInput" id="fileInput"
            accept=".docx" hidden @change="selectFile">
        </div>
        <div id="notice" class="notice" :class="notice.kind" v-show="notice.text">{{ notice.text }}</div>
        <div id="list" class="tpl-grid">
          <div class="empty" v-if="!templates.length">还没有模板。点右上角「上传模板」添加一套 .docx 招标文件范本。</div>
          <article v-for="template in templates" :key="template.id" class="card tpl-card">
            <h3>{{ template.name }}</h3>
            <div class="badges"><span class="tag" :class="template.builtin ? 'work' : 'ok'">{{ template.builtin ? '内置 ·只读' : '自有模板' }}</span><span class="tag ok" v-if="template.is_default">默认</span></div>
            <div class="tpl-meta">{{ category[template.category] || template.category || '—' }} · {{
              method[template.method] || template.method || '—' }} · v{{ template.version || '1.0' }}<br>
              占位符 {{ template.placeholder_count ?? (template.placeholders || []).length }} 个 · <b
                v-if="template.unmapped?.length">未匹配 {{ template.unmapped.length }} 个</b><template
                v-else>全部已匹配</template>
              <b v-if="template.missing_fields?.length"> · 缺 {{ template.missing_fields.length }} 个字段位</b><br><span
                class="dim">{{
                  String(template.created_at || '').slice(0, 10) }}</span>
            </div>
            <div class="tpl-actions"><button class="btn small ghost" data-act="preview" :data-id="template.id"
                @click="showPreview(template)">预览</button><button class="btn small ghost" data-act="detail"
                :data-id="template.id" @click="showDetail(template)">占位符</button>
              <button class="btn small ghost" v-if="!template.is_default" :disabled="busy"
                @click="setDefault(template)">设为默认</button><template v-if="!template.builtin"><button
                  class="btn small ghost" @click="openRename(template)">重命名</button><button
                  class="btn small ghost danger" @click="askDelete(template)">删除</button></template>
            </div>
          </article>
        </div>
        <footer>内置模板随程序分发；上传的模板保存在本机 data/templates/，不会上传到外部服务</footer>
      </div>
    </main>
  </div>
  <dialog id="dlgUpload">
    <div class="dlg-body">
      <h3>上传招标文件模板</h3>
      <div class="upload-hint" id="uploadFile">{{ pendingFile ? pendingFile.name + '（' + Math.round(pendingFile.size /
        1024) + ' KB）' : '' }}</div>
      <label>模板名称</label><input id="upName" maxlength="80" v-model="uploadForm.name"><label>品目类别</label><select
        id="upCategory" v-model="uploadForm.category">
        <option v-for="(label, key) in category" :key="key" :value="key">{{ label }}</option>
      </select>
      <label>采购方式</label><select id="upMethod" v-model="uploadForm.method">
        <option v-for="(label, key) in method" :key="key" :value="key">{{ label }}</option>
      </select>
      <div class="upload-hint">占位符请写成单花括号，例如 <code>{项目名称}</code>。上传后会自动分析哪些占位符还没有对应字段。</div>
      <div class="dlg-foot"><button class="btn" :disabled="busy" @click="dialog('dlgUpload', false)">取消</button><button
          class="btn primary" id="upSave" :disabled="busy" @click="upload">{{ busy ? '解析中…' : '上传并解析' }}</button></div>
    </div>
  </dialog>
  <dialog id="dlgRename">
    <div class="dlg-body">
      <h3>重命名模板</h3><label>模板名称</label><input id="rnName" maxlength="80" v-model="rename.name">
      <div class="dlg-foot"><button class="btn" @click="dialog('dlgRename', false)">取消</button><button
          class="btn primary" id="rnSave" :disabled="busy" @click="saveRename">保存</button></div>
    </div>
  </dialog>
  <dialog id="dlgConfirm">
    <div class="dlg-body">
      <h3 id="cfTitle">删除模板</h3>
      <p id="cfText" style="font-size:13px;color:var(--muted);line-height:1.7">确定删除「{{ deleting?.name
        }}」吗？已生成的招标文件不受影响；如果它是默认模板，会自动回退到内置模板。</p>
      <div class="dlg-foot"><button class="btn" @click="dialog('dlgConfirm', false)">取消</button><button
          class="btn primary" id="cfOk" :disabled="busy" @click="remove">确认</button></div>
    </div>
  </dialog>
  <dialog id="dlgDetail">
    <div class="dlg-body">
      <h3 id="dtTitle">{{ selected?.name || '模板详情' }}</h3>
      <p style="font-size:12.5px;color:var(--muted);line-height:1.7">标为「未匹配」的占位符，可到填写工作台通过「＋ 自定义字段」绑定。</p>
      <div class="ph-list" id="dtList">
        <div class="row" v-for="name in selected?.placeholders || []" :key="name"><code>{{ '{' + name + '}' }}</code>
          <div class="spacer"></div><span class="tag" :class="selected.unmapped.includes(name) ? 'low' : 'ok'">{{
            selected.unmapped.includes(name) ? '未匹配字段' : '已匹配' }}</span>
        </div>
        <div class="empty" v-if="!selected?.placeholders?.length">这套模板没有解析到占位符。</div>
      </div>
      <div class="dlg-foot"><button class="btn" @click="dialog('dlgDetail', false)">关闭</button></div>
    </div>
  </dialog>
  <dialog id="dlgPreview" class="dlg-preview" @close="invalidatePreview">
    <div class="dlg-body">
      <h3 id="pvTitle">预览 · {{ previewName }}</h3>
      <div class="preview-scroll" id="pvBody">
        <div class="paper" id="pvPaper">
          <p class="pv-body" v-if="previewMessage">{{ previewMessage }}</p>
          <DocumentPreview v-else :sections="previewSections"></DocumentPreview>
        </div>
      </div>
      <p class="pv-hint">预览为结构近似，字体与分页以生成的 .docx 为准；黄色标记为占位符。</p>
      <div class="dlg-foot"><button class="btn" @click="dialog('dlgPreview', false)">关闭</button></div>
    </div>
  </dialog>

</template>
