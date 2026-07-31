import { defineConfig } from "vitepress";
import { withMermaid } from "vitepress-plugin-mermaid2";

const repositoryUrl = "https://github.com/INSide-734/astrbot_plugin_memora";
const docsBase = process.env.DOCS_BASE ?? "/";

export default withMermaid(
  defineConfig({
    lang: "zh-CN",
    title: "Memora",
    titleTemplate: ":title | Memora 文档",
    description: "Memora 是面向 AstrBot 的长期记忆插件。",
    base: docsBase,
    head: [
      ["link", { rel: "icon", type: "image/png", href: `${docsBase}logo.png` }],
    ],
    cleanUrls: true,
    lastUpdated: true,
    appearance: true,
    markdown: {
      lineNumbers: true,
    },
    themeConfig: {
      logo: {
        src: "/logo.png",
        alt: "Memora",
      },
      nav: [
        { text: "指南", link: "/guide/introduction" },
        { text: "配置参考", link: "/reference/configuration" },
        {
          text: "更新日志",
          link: `${repositoryUrl}/blob/main/CHANGELOG.md`,
        },
      ],
      sidebar: [
        {
          text: "开始使用",
          items: [
            { text: "项目介绍", link: "/guide/introduction" },
            { text: "快速开始", link: "/guide/getting-started" },
            { text: "配置入门", link: "/guide/configuration" },
            {
              text: "质量与成本档位",
              link: "/guide/tuning-profiles",
            },
          ],
        },
        {
          text: "核心概念",
          items: [
            { text: "架构导览", link: "/concepts/architecture" },
            { text: "记忆生命周期", link: "/concepts/memory-lifecycle" },
            { text: "稳定身份", link: "/concepts/identity" },
            {
              text: "检索与注入",
              link: "/concepts/retrieval-injection",
            },
          ],
        },
        {
          text: "功能指南",
          items: [
            { text: "Dashboard", link: "/features/dashboard" },
            { text: "Agent 工具", link: "/features/agent-tools" },
            { text: "Page API", link: "/features/page-api" },
          ],
        },
        {
          text: "运维",
          items: [
            { text: "故障排除", link: "/operations/troubleshooting" },
            { text: "备份与恢复", link: "/operations/backup-recovery" },
            {
              text: "诊断与评测",
              link: "/operations/diagnostics-evaluation",
            },
            { text: "在线更新", link: "/operations/update" },
          ],
        },
        {
          text: "参考",
          items: [
            { text: "管理命令", link: "/reference/commands" },
            {
              text: "配置参考",
              link: "/reference/configuration",
              collapsed: false,
              items: [
                {
                  text: "基础运行与记忆生成",
                  link: "/reference/configuration/basic",
                },
                {
                  text: "召回、注入与索引",
                  link: "/reference/configuration/retrieval",
                },
                {
                  text: "记忆生命周期",
                  link: "/reference/configuration/lifecycle",
                },
                {
                  text: "智能与内容增强",
                  link: "/reference/configuration/features",
                },
                {
                  text: "运维、可靠性与安全",
                  link: "/reference/configuration/operations",
                },
              ],
            },
          ],
        },
        {
          text: "开发",
          items: [
            { text: "环境准备", link: "/development/setup" },
            { text: "质量门禁", link: "/development/quality-gates" },
          ],
        },
      ],
      outline: {
        level: [2, 3],
        label: "本页目录",
      },
      search: {
        provider: "local",
        options: {
          translations: {
            button: {
              buttonText: "搜索文档",
              buttonAriaLabel: "搜索文档",
            },
            modal: {
              noResultsText: "没有找到相关内容",
              resetButtonTitle: "清除查询条件",
              footer: {
                selectText: "选择",
                navigateText: "切换",
                closeText: "关闭",
              },
            },
          },
        },
      },
      socialLinks: [{ icon: "github", link: repositoryUrl }],
      editLink: {
        pattern: `${repositoryUrl}/edit/main/website/docs/:path`,
        text: "在 GitHub 上编辑此页",
      },
      lastUpdated: {
        text: "最后更新",
        formatOptions: {
          dateStyle: "medium",
          timeStyle: "short",
        },
      },
      docFooter: {
        prev: "上一页",
        next: "下一页",
      },
      darkModeSwitchLabel: "切换深色模式",
      lightModeSwitchTitle: "切换浅色模式",
      darkModeSwitchTitle: "切换深色模式",
      sidebarMenuLabel: "文档导航",
      returnToTopLabel: "返回顶部",
      footer: {
        message: "Memora 文档随当前实现持续维护",
        copyright: "基于 AGPL-3.0 许可证发布",
      },
    },
  }),
);
