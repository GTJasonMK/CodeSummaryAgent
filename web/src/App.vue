<template>
  <div class="min-h-screen bg-gray-50">
    <!-- 顶部导航 -->
    <header class="bg-white shadow-sm border-b border-gray-200">
      <div class="max-w-7xl mx-auto px-4 py-4 flex items-center justify-between">
        <div class="flex items-center space-x-3">
          <div class="w-10 h-10 bg-primary-500 rounded-lg flex items-center justify-center">
            <svg class="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4" />
            </svg>
          </div>
          <div>
            <h1 class="text-xl font-bold text-gray-800">CodeSummaryAgent</h1>
            <p class="text-sm text-gray-500">基于LLM的代码库分析工具</p>
          </div>
        </div>

        <nav class="flex space-x-4">
          <router-link
            to="/"
            class="px-3 py-2 rounded-md text-sm font-medium"
            :class="$route.path === '/' ? 'bg-primary-100 text-primary-700' : 'text-gray-600 hover:bg-gray-100'"
          >
            分析面板
          </router-link>
          <router-link
            to="/tasks"
            class="px-3 py-2 rounded-md text-sm font-medium"
            :class="$route.path === '/tasks' ? 'bg-primary-100 text-primary-700' : 'text-gray-600 hover:bg-gray-100'"
          >
            任务列表
          </router-link>
        </nav>
      </div>
    </header>

    <!-- 主内容区域 -->
    <main class="max-w-7xl mx-auto px-4 py-6">
      <router-view v-slot="{ Component }">
        <transition name="fade" mode="out-in">
          <component :is="Component" />
        </transition>
      </router-view>
    </main>

    <!-- 底部状态栏 -->
    <footer class="fixed bottom-0 left-0 right-0 bg-white border-t border-gray-200 py-2 px-4">
      <div class="max-w-7xl mx-auto flex items-center justify-between text-sm text-gray-500">
        <div class="flex items-center space-x-2">
          <span class="w-2 h-2 rounded-full" :class="connectionStatus ? 'bg-green-500' : 'bg-red-500'"></span>
          <span>{{ connectionStatus ? '已连接' : '未连接' }}</span>
        </div>
        <span>CodeSummaryAgent v1.0.0</span>
      </div>
    </footer>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { useAnalysisStore } from '@/stores/analysis'

const store = useAnalysisStore()
const connectionStatus = ref(false)

onMounted(() => {
  // 检查API连接状态
  checkConnection()
})

async function checkConnection() {
  try {
    const response = await fetch('/api/health')
    connectionStatus.value = response.ok
  } catch {
    connectionStatus.value = false
  }
}
</script>
