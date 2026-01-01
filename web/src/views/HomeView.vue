<template>
  <div class="space-y-6">
    <!-- 分析控制面板 -->
    <div class="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
      <h2 class="text-lg font-semibold text-gray-800 mb-4">开始分析</h2>

      <div class="space-y-4">
        <!-- 路径输入 -->
        <div>
          <label class="block text-sm font-medium text-gray-700 mb-1">
            源代码目录
          </label>
          <div class="flex space-x-2">
            <input
              v-model="sourcePath"
              type="text"
              placeholder="输入代码目录路径，例如: C:/projects/my-app"
              class="flex-1 px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-transparent"
            />
            <button
              @click="handleScan"
              :disabled="!sourcePath || isLoading"
              class="px-4 py-2 bg-gray-100 text-gray-700 rounded-lg hover:bg-gray-200 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              预览
            </button>
          </div>
        </div>

        <!-- 选项 -->
        <div class="flex items-center space-x-6">
          <label class="flex items-center space-x-2">
            <input v-model="options.resume" type="checkbox" class="rounded text-primary-500" />
            <span class="text-sm text-gray-600">启用断点续传</span>
          </label>
        </div>

        <!-- 开始按钮 -->
        <button
          @click="handleAnalyze"
          :disabled="!sourcePath || isLoading"
          class="w-full px-4 py-3 bg-primary-500 text-white font-medium rounded-lg hover:bg-primary-600 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          <span v-if="isLoading" class="flex items-center justify-center">
            <svg class="animate-spin -ml-1 mr-2 h-5 w-5" fill="none" viewBox="0 0 24 24">
              <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
              <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
            </svg>
            处理中...
          </span>
          <span v-else>开始分析</span>
        </button>
      </div>
    </div>

    <!-- 目录预览 -->
    <div v-if="fileTree" class="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
      <div class="flex items-center justify-between mb-4">
        <h2 class="text-lg font-semibold text-gray-800">目录结构预览</h2>
        <div class="text-sm text-gray-500">
          {{ stats.total_files }} 个文件 · {{ stats.total_dirs }} 个目录 · 深度 {{ stats.max_depth }}
        </div>
      </div>

      <FileTree :node="fileTree" />
    </div>

    <!-- 运行中任务 -->
    <div v-if="runningTasks.length > 0" class="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
      <h2 class="text-lg font-semibold text-gray-800 mb-4">运行中任务</h2>

      <div class="space-y-4">
        <TaskCard
          v-for="task in runningTasks"
          :key="task.task_id"
          :task="task"
          @cancel="store.cancelTask(task.task_id)"
        />
      </div>
    </div>

    <!-- 错误提示 -->
    <div v-if="error" class="bg-red-50 border border-red-200 rounded-lg p-4">
      <div class="flex items-center space-x-2 text-red-700">
        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
            d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
        <span>{{ error }}</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAnalysisStore } from '@/stores/analysis'
import FileTree from '@/components/FileTree.vue'
import TaskCard from '@/components/TaskCard.vue'

const router = useRouter()
const store = useAnalysisStore()

const sourcePath = ref('')
const options = ref({
  resume: true,
})

const fileTree = ref(null)
const stats = ref({})
const isLoading = computed(() => store.isLoading)
const error = computed(() => store.error)
const runningTasks = computed(() => store.runningTasks)

onMounted(() => {
  store.fetchTasks()
})

async function handleScan() {
  try {
    const result = await store.scanDirectory(sourcePath.value)
    fileTree.value = result.root
    stats.value = result.stats
  } catch (e) {
    console.error('扫描失败:', e)
  }
}

async function handleAnalyze() {
  try {
    const result = await store.startAnalysis(sourcePath.value, options.value)
    if (result.task_id) {
      router.push(`/task/${result.task_id}`)
    }
  } catch (e) {
    console.error('启动分析失败:', e)
  }
}
</script>
