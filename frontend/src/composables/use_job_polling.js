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
    async function start(job) {
      stop();
      const current = generation;
      progress.job = job;
      progress.percent = Number(job.progress) || 0;
      progress.running = true;
      progress.stage = job.stage || "处理中";
      // 任务公开接口通常返回 elapsed 而不返回 created_at，刷新后从服务端耗时继续计时。
      startedAt = job.created_at ? Number(job.created_at) * 1000 : Date.now() - (Number(job.elapsed) || 0) * 1000;
      if (!Number.isFinite(startedAt) || startedAt <= 0) startedAt = Date.now();
      tick = setInterval(() => { progress.elapsed = Math.max(0, (Date.now() - startedAt) / 1000).toFixed(1); }, 100);
      async function poll() {
        try {
          const data = await api("/api/jobs/" + encodeURIComponent(job.id));
          if (current !== generation) return;
          progress.job = data;
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
