<template>
  <div class="font-mono text-sm">
    <div
      class="flex items-center py-1 px-2 rounded hover:bg-gray-50 cursor-pointer select-none"
      :style="{ paddingLeft: `${depth * 20 + 8}px` }"
      @click="toggleExpand"
    >
      <!-- 展开/折叠图标 -->
      <span v-if="node.children?.length" class="w-4 h-4 mr-1 flex items-center justify-center">
        <svg
          class="w-3 h-3 text-gray-400 transition-transform"
          :class="{ 'rotate-90': isExpanded }"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
        </svg>
      </span>
      <span v-else class="w-4 h-4 mr-1"></span>

      <!-- 文件/目录图标 -->
      <span class="mr-2">
        <svg v-if="node.type === 'directory'" class="w-4 h-4 text-yellow-500" fill="currentColor" viewBox="0 0 20 20">
          <path d="M2 6a2 2 0 012-2h5l2 2h5a2 2 0 012 2v6a2 2 0 01-2 2H4a2 2 0 01-2-2V6z" />
        </svg>
        <svg v-else class="w-4 h-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
            d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
        </svg>
      </span>

      <!-- 文件名 -->
      <span class="flex-1" :class="getNameClass">{{ node.name }}</span>

      <!-- 状态指示器 -->
      <span v-if="showStatus" class="ml-2">
        <span v-if="node.status === 'completed'" class="text-green-500">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" />
          </svg>
        </span>
        <span v-else-if="node.status === 'failed'" class="text-red-500">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </span>
        <span v-else-if="node.status === 'in_progress'" class="text-blue-500">
          <svg class="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
          </svg>
        </span>
        <span v-else class="text-gray-300">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <circle cx="12" cy="12" r="10" stroke-width="2" />
          </svg>
        </span>
      </span>
    </div>

    <!-- 子节点 -->
    <transition name="slide">
      <div v-if="isExpanded && node.children?.length">
        <FileTree
          v-for="child in sortedChildren"
          :key="child.path"
          :node="child"
          :depth="depth + 1"
          :showStatus="showStatus"
        />
      </div>
    </transition>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'

const props = defineProps({
  node: {
    type: Object,
    required: true,
  },
  depth: {
    type: Number,
    default: 0,
  },
  showStatus: {
    type: Boolean,
    default: false,
  },
})

const isExpanded = ref(props.depth < 2) // 默认展开前两层

const sortedChildren = computed(() => {
  if (!props.node.children) return []
  // 目录在前，文件在后
  return [...props.node.children].sort((a, b) => {
    if (a.type === 'directory' && b.type !== 'directory') return -1
    if (a.type !== 'directory' && b.type === 'directory') return 1
    return a.name.localeCompare(b.name)
  })
})

const getNameClass = computed(() => {
  if (props.node.type === 'directory') {
    return 'font-medium text-gray-800'
  }
  return 'text-gray-600'
})

function toggleExpand() {
  if (props.node.children?.length) {
    isExpanded.value = !isExpanded.value
  }
}
</script>

<style scoped>
.slide-enter-active,
.slide-leave-active {
  transition: all 0.2s ease;
}

.slide-enter-from,
.slide-leave-to {
  opacity: 0;
  transform: translateY(-10px);
}
</style>
