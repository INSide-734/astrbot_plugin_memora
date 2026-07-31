---
layout: home

hero:
  name: Memora
  text: 让重要的对话，成为可以延续的记忆
  tagline: 面向 AstrBot 的完整长期记忆系统：可靠保存、准确召回，并在身份、隐私与预算边界内安全使用。
  actions:
    - theme: brand
      text: 了解 Memora
      link: /guide/introduction
    - theme: alt
      text: 快速开始
      link: /guide/getting-started

features:
  - title: 认识记忆系统
    details: 理解记忆如何形成、保存、检索，并在明确边界内提供给模型。
    link: /guide/introduction
    linkText: 阅读项目介绍
  - title: 完成首次部署
    details: 安装插件、连接 Provider，并通过状态检查确认核心组件已经就绪。
    link: /guide/getting-started
    linkText: 查看快速开始
  - title: 管理长期数据
    details: 掌握 Dashboard、健康诊断、备份恢复以及索引重建等日常操作。
    link: /operations/backup-recovery
    linkText: 进入运维指南
---

<section class="home-story" aria-labelledby="home-story-title">
  <div class="home-story-heading">
    <p class="home-kicker">不只是向量检索</p>
    <h2 id="home-story-title">把长期记忆变成一条<br>可管理的系统链路</h2>
  </div>
  <div class="home-story-content">
    <p class="home-story-lead">Memora 从日常对话中提取值得长期保留的事实、偏好、关系和经历，在后续请求真正需要时，再按身份、场景、隐私和预算约束召回。</p>
    <p>从记忆形成到安全注入，每个阶段都有清晰边界。某个增强模块暂时不可用时，权威数据仍然保留，AstrBot 的聊天主链路也能继续运行。</p>
    <dl class="home-principles">
      <div>
        <dt>唯一权威</dt>
        <dd>SQLite canonical memory 保存事实来源，索引与关系数据都可以重建。</dd>
      </div>
      <div>
        <dt>请求级注入</dt>
        <dd>动态记忆只进入当前请求，不写入 System Prompt。</dd>
      </div>
      <div>
        <dt>边界优先</dt>
        <dd>身份、作用域、隐私与有效期共同决定一条记忆能否被使用。</dd>
      </div>
    </dl>
    <nav class="home-story-links" aria-label="继续了解 Memora">
      <a href="./concepts/memory-lifecycle">记忆生命周期 <span aria-hidden="true">→</span></a>
      <a href="./concepts/identity">稳定身份 <span aria-hidden="true">→</span></a>
      <a href="./concepts/retrieval-injection">检索与注入 <span aria-hidden="true">→</span></a>
    </nav>
  </div>
</section>
