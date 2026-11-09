import { reactive, onBeforeUnmount } from 'vue';
import { api } from '../services/api.js';

  /** 页面离开时停止计时和轮询，防止旧任务响应污染当前页面。 */
  export function useJobPolling(onDone, onError) {
    const progress = reactive({ running: false, stage: "", percent: 0, elapsed: "0.0", job: null });
    let timer = null, tick = null, generation = 0, startedAt = 0;
    function stop() {
      generation += 1;
      clearTimeout(timer); clearInterval(tick);
      progress.running = false;
    }
    /** 服务端 elapsed 按任务创建时间计算，是切页恢复与浏览器节流后唯一可信的基准。
     *  每次轮询都校准本地起点，避免累计漂移；缺有效值时保留已有基准，不让读秒跳回 0。 */
    function syncElapsed(job) {
      const elapsed = Number(job?.elapsed);
      if (!Number.isFinite(elapsed) || elapsed < 0) {
        if (!startedAt) startedAt = Date.now();
        return;
      }
      startedAt = Date.now() - elapsed * 1000;
      progress.elapsed = elapsed.toFixed(1);
    }
    async function start(job) {
      stop();
      const current = generation;
      progress.job = job;
      progress.percent = Number(job.progress) || 0;
      progress.running = true;
      progress.stage = job.stage || "处理中";
      syncElapsed(job);
      tick = setInterval(() => { progress.elapsed = Math.max(0, (Date.now() - startedAt) / 1000).toFixed(1); }, 100);
      async function poll() {
        try {
          const data = await api("/api/jobs/" + encodeURIComponent(job.id));
          if (current !== generation) return;
          progress.job = data;
          syncElapsed(data);
          // 百分比由后端阶段回传；不按计时器虚增，兼容未提供进度的旧任务。
          progress.percent = ['done', 'skipped'].includes(data.status) ? 100 : Math.max(progress.percent, Math.min(99, Number(data.progress) || 0));
          progress.stage = data.stage || "处理中";
          if (["queued", "running"].includes(data.status)) { timer = setTimeout(poll, 900); return; }
          clearTimeout(timer); clearInterval(tick); progress.running = false;
          if (data.status === "error") throw new Error(data.error || "任务失败");
          await onDone(data);
        } catch (error) {
          if (current !== generation) return;
          stop(); onError(error.message);
        }
      }
      await poll();
    }
    onBeforeUnmount(stop);
    window.addEventListener("pagehide", stop, { once: true });
    return { progress, start, stop };
  }
