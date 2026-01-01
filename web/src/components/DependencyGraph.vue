<template>
  <div class="bg-white rounded-lg border border-gray-200 p-6">
    <h3 class="text-lg font-semibold text-gray-800 mb-4">依赖关系图</h3>

    <!-- 控制面板 -->
    <div class="flex items-center space-x-4 mb-4">
      <select
        v-model="viewMode"
        class="px-3 py-1.5 border border-gray-300 rounded-lg text-sm"
      >
        <option value="all">全部依赖</option>
        <option value="internal">内部依赖</option>
        <option value="external">外部依赖</option>
      </select>

      <button
        @click="resetView"
        class="px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-100 rounded-lg"
      >
        重置视图
      </button>
    </div>

    <!-- 图形容器 -->
    <div
      ref="graphContainer"
      class="w-full border border-gray-200 rounded-lg bg-gray-50 overflow-hidden"
      :style="{ height: `${height}px` }"
    >
      <!-- 使用Mermaid或自定义SVG渲染 -->
      <div v-if="!graph" class="flex items-center justify-center h-full text-gray-400">
        暂无依赖数据
      </div>
      <div v-else class="p-4">
        <pre class="text-xs text-gray-600 overflow-auto">{{ mermaidCode }}</pre>
      </div>
    </div>

    <!-- 统计信息 -->
    <div v-if="graph" class="mt-4 grid grid-cols-3 gap-4 text-center">
      <div class="bg-gray-50 rounded-lg p-3">
        <div class="text-xl font-bold text-gray-800">{{ graph.nodes?.length || 0 }}</div>
        <div class="text-sm text-gray-500">节点数</div>
      </div>
      <div class="bg-gray-50 rounded-lg p-3">
        <div class="text-xl font-bold text-gray-800">{{ graph.edges?.length || 0 }}</div>
        <div class="text-sm text-gray-500">依赖数</div>
      </div>
      <div class="bg-gray-50 rounded-lg p-3">
        <div class="text-xl font-bold text-gray-800">{{ externalDeps }}</div>
        <div class="text-sm text-gray-500">外部依赖</div>
      </div>
    </div>

    <!-- 依赖列表 -->
    <div v-if="graph?.edges?.length" class="mt-4">
      <h4 class="text-sm font-medium text-gray-700 mb-2">依赖详情</h4>
      <div class="max-h-60 overflow-auto">
        <table class="w-full text-sm">
          <thead class="bg-gray-50">
            <tr>
              <th class="px-3 py-2 text-left text-gray-600">源文件</th>
              <th class="px-3 py-2 text-left text-gray-600">依赖</th>
              <th class="px-3 py-2 text-left text-gray-600">类型</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-gray-100">
            <tr v-for="(edge, index) in displayedEdges" :key="index" class="hover:bg-gray-50">
              <td class="px-3 py-2 font-mono text-xs">{{ getFileName(edge.source) }}</td>
              <td class="px-3 py-2 font-mono text-xs">{{ edge.target }}</td>
              <td class="px-3 py-2">
                <span
                  class="px-2 py-0.5 rounded text-xs"
                  :class="getTypeClass(edge.type)"
                >
                  {{ edge.type }}
                </span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'

const props = defineProps({
  graph: {
    type: Object,
    default: null,
  },
  height: {
    type: Number,
    default: 400,
  },
})

const graphContainer = ref(null)
const viewMode = ref('all')

const displayedEdges = computed(() => {
  if (!props.graph?.edges) return []

  let edges = props.graph.edges

  if (viewMode.value === 'internal') {
    edges = edges.filter(e => !e.target.startsWith('.') && !e.target.includes('/'))
  } else if (viewMode.value === 'external') {
    edges = edges.filter(e => e.target.startsWith('.') || e.target.includes('/'))
  }

  return edges.slice(0, 100) // 限制显示数量
})

const externalDeps = computed(() => {
  if (!props.graph?.edges) return 0
  const external = new Set()
  props.graph.edges.forEach(e => {
    if (!props.graph.nodes?.includes(e.target)) {
      external.add(e.target.split('.')[0].split('/')[0])
    }
  })
  return external.size
})

const mermaidCode = computed(() => {
  if (!props.graph) return ''

  const lines = ['graph LR']
  const nodeIds = {}

  props.graph.nodes?.forEach((node, i) => {
    nodeIds[node] = `N${i}`
    const name = node.split(/[/\\]/).pop() || node
    lines.push(`    N${i}["${name}"]`)
  })

  displayedEdges.value.forEach(edge => {
    const sourceId = nodeIds[edge.source] || edge.source
    const targetId = nodeIds[edge.target] || edge.target
    if (sourceId && targetId) {
      lines.push(`    ${sourceId} --> ${targetId}`)
    }
  })

  return lines.join('\n')
})

function getFileName(path) {
  return path.split(/[/\\]/).pop() || path
}

function getTypeClass(type) {
  const map = {
    import: 'bg-blue-100 text-blue-700',
    extends: 'bg-purple-100 text-purple-700',
    implements: 'bg-green-100 text-green-700',
    uses: 'bg-yellow-100 text-yellow-700',
  }
  return map[type] || 'bg-gray-100 text-gray-700'
}

function resetView() {
  viewMode.value = 'all'
}
</script>
