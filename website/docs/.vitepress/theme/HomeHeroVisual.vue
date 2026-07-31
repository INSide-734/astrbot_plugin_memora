<template>
  <div
    class="home-hero-visual"
    role="img"
    aria-label="对话经过记忆形成和权威存储后，在身份、隐私与预算约束下安全召回"
  >
    <div aria-hidden="true">
      <div class="visual-header">
        <span class="visual-title">长期记忆链路</span>
        <span class="visual-status"><i></i>运行时闭环</span>
      </div>

      <div class="visual-pipeline">
        <div class="pipeline-step">
          <span class="step-index">01</span>
          <span class="step-copy">
            <strong>记忆形成</strong>
            <small>提取 · 去重 · 反思</small>
          </span>
          <span class="step-kind">输入</span>
        </div>

        <div class="pipeline-connector"><i></i></div>

        <div class="pipeline-step is-canonical">
          <span class="step-index">02</span>
          <span class="step-copy">
            <strong>权威记忆</strong>
            <small>SQLite canonical memory</small>
          </span>
          <span class="step-kind">唯一来源</span>
        </div>

        <div class="pipeline-connector has-branches">
          <i></i>
          <span>全文</span>
          <span>向量</span>
          <span>图关系</span>
        </div>

        <div class="pipeline-step">
          <span class="step-index">03</span>
          <span class="step-copy">
            <strong>安全召回</strong>
            <small>融合 · 过滤 · 请求级注入</small>
          </span>
          <span class="step-kind">输出</span>
        </div>
      </div>

      <div class="visual-boundaries">
        <span>身份边界</span>
        <span>隐私约束</span>
        <span>注入预算</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.home-hero-visual {
  width: min(100%, 28rem);
  margin: 0 auto;
  overflow: hidden;
  border: 1px solid var(--vp-c-divider);
  border-radius: 8px;
  background: var(--vp-c-bg-alt);
  color: var(--vp-c-text-1);
}

.visual-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 3rem;
  padding: 0 1rem;
  border-bottom: 1px solid var(--vp-c-divider);
  background: var(--vp-c-bg-elv);
}

.visual-title,
.visual-status,
.step-kind,
.visual-boundaries span {
  font-size: 0.75rem;
  font-weight: 650;
}

.visual-status {
  display: inline-flex;
  gap: 0.5rem;
  align-items: center;
  color: var(--vp-c-text-2);
}

.visual-status i {
  width: 0.5rem;
  height: 0.5rem;
  border-radius: 50%;
  background: var(--vp-c-tip-1);
}

.visual-pipeline {
  padding: 1.5rem;
}

.pipeline-step {
  display: grid;
  grid-template-columns: 2rem minmax(0, 1fr) auto;
  gap: 0.75rem;
  align-items: center;
  min-height: 4rem;
  padding: 0.75rem;
}

.pipeline-step.is-canonical {
  border: 1px solid var(--vp-c-border);
  border-radius: 6px;
  background: var(--vp-c-bg-elv);
}

.step-index {
  color: var(--vp-c-text-3);
  font-size: 0.75rem;
  font-variant-numeric: tabular-nums;
  font-weight: 650;
}

.step-copy {
  display: grid;
  gap: 0.25rem;
  min-width: 0;
}

.step-copy strong {
  font-size: 0.9375rem;
  line-height: 1.35;
}

.step-copy small {
  overflow: hidden;
  color: var(--vp-c-text-2);
  font-size: 0.75rem;
  line-height: 1.45;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.step-kind {
  color: var(--vp-c-text-3);
  white-space: nowrap;
}

.pipeline-connector {
  display: flex;
  align-items: center;
  min-height: 1.75rem;
  padding-left: 1.75rem;
}

.pipeline-connector i {
  width: 1px;
  height: 1.75rem;
  background: var(--vp-c-divider);
}

.pipeline-connector.has-branches {
  gap: 0.5rem;
  min-height: 2.25rem;
}

.pipeline-connector.has-branches span {
  padding: 0.25rem 0.5rem;
  border: 1px solid var(--vp-c-divider);
  border-radius: 4px;
  background: var(--vp-c-bg);
  color: var(--vp-c-text-2);
  font-size: 0.6875rem;
  line-height: 1.2;
  white-space: nowrap;
}

.visual-boundaries {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  border-top: 1px solid var(--vp-c-divider);
  background: var(--vp-c-bg-elv);
}

.visual-boundaries span {
  padding: 0.75rem 0.5rem;
  color: var(--vp-c-text-2);
  text-align: center;
}

.visual-boundaries span + span {
  border-left: 1px solid var(--vp-c-divider);
}

@media (max-width: 420px) {
  .visual-pipeline {
    padding: 1rem;
  }

  .pipeline-step {
    grid-template-columns: 1.75rem minmax(0, 1fr);
  }

  .step-kind {
    display: none;
  }

  .pipeline-connector.has-branches {
    gap: 0.25rem;
    padding-left: 1.5rem;
  }

  .pipeline-connector.has-branches span {
    padding-inline: 0.375rem;
  }
}
</style>
