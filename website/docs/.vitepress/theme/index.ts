import { h } from "vue";
import DefaultTheme from "vitepress/theme";

import HomeHeroVisual from "./HomeHeroVisual.vue";
import "./custom.css";

/** 渲染首页 Hero 的长期记忆链路示意。 */
function renderHomeHeroVisual() {
  return h(HomeHeroVisual);
}

/** 渲染 VitePress 默认布局并注入首页专属内容。 */
function renderLayout() {
  return h(DefaultTheme.Layout, null, {
    "home-hero-image": renderHomeHeroVisual,
  });
}

export default {
  extends: DefaultTheme,
  Layout: renderLayout,
};
