<script setup lang="ts">
import { ref, watch } from 'vue'
import { api } from '../api'

const props = defineProps<{
  modelValue: string
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void
}>()

const formula = ref(props.modelValue)
const testValue = ref(100)
const testResult = ref<number | null>(null)
const testError = ref<string | null>(null)
const testing = ref(false)
const showHelp = ref(false)

watch(() => props.modelValue, (newVal) => {
    if (newVal !== formula.value) {
        formula.value = newVal
    }
})

watch(formula, (newVal) => {
    emit('update:modelValue', newVal)
    testResult.value = null
    testError.value = null
})

// Toolbox definitions
const toolbox = {
    vars: ['val'],
    basic: ['+', '-', '*', '/', '(', ')', '%'],
    math: ['round', 'abs', 'min', 'max', 'pow', 'sqrt'],
    trig: ['sin', 'cos', 'tan', 'pi'],
    logic: ['if ... else'] // This is a snippet
}

function insertToken(token: string) {
    const textarea = document.querySelector('.formula-input') as HTMLTextAreaElement
    if (!textarea) return

    let textToInsert = token
    if (token === 'if ... else') {
        textToInsert = 'val if val > 10 else 0'
    } else if (['round', 'min', 'max', 'pow', 'sqrt', 'sin', 'cos', 'tan', 'abs'].includes(token)) {
        textToInsert = `${token}(`
    }

    const start = textarea.selectionStart
    const end = textarea.selectionEnd
    const text = formula.value
    
    formula.value = text.substring(0, start) + textToInsert + text.substring(end)
    
    // Defer focus back to textarea
    setTimeout(() => {
        textarea.focus()
        textarea.selectionStart = textarea.selectionEnd = start + textToInsert.length
    }, 0)
}

async function testFormula() {
    testing.value = true
    testError.value = null
    testResult.value = null
    
    try {
        const response = await api.post('/api/sensors/test-formula', {
            formula: formula.value,
            test_value: testValue.value
        })
        
        if (response.data.valid) {
            testResult.value = response.data.result
        } else {
            testError.value = response.data.error || 'Unknown validation error'
        }
    } catch (e: any) {
        testError.value = e.response?.data?.detail || e.message
    } finally {
        testing.value = false
    }
}
</script>

<template>
  <div class="formula-editor">
    
    <!-- Toolbox -->
    <div class="toolbox">
        <div class="toolbox-category">
            <span class="category-label">Vars</span>
            <button v-for="t in toolbox.vars" :key="t" @click="insertToken(t)" class="tool-btn var-btn">{{ t }}</button>
        </div>
        <div class="toolbox-category">
            <span class="category-label">Basic</span>
            <button v-for="t in toolbox.basic" :key="t" @click="insertToken(t)" class="tool-btn">{{ t }}</button>
        </div>
        <div class="toolbox-category">
            <span class="category-label">Math</span>
            <button v-for="t in toolbox.math" :key="t" @click="insertToken(t)" class="tool-btn">{{ t }}</button>
        </div>
        <div class="toolbox-category">
            <span class="category-label">Trig</span>
            <button v-for="t in toolbox.trig" :key="t" @click="insertToken(t)" class="tool-btn">{{ t }}</button>
        </div>
    </div>

    <!-- Editor Area -->
    <div class="editor-container">
        <textarea 
            v-model="formula" 
            class="formula-input" 
            rows="3" 
            placeholder="e.g. val * 0.1 + 10"
        ></textarea>
        <button type="button" class="help-toggle" @click="showHelp = !showHelp">
            <i class="pi pi-question-circle"></i>
        </button>
    </div>

    <!-- Help Section -->
    <div v-if="showHelp" class="help-box">
        <h4>Formula Guide</h4>
        <p>Use Python-like syntax. Variable <code>val</code> is the raw sensor reading.</p>
        <div class="example">
            <strong>Complex Example:</strong>
            <code>round(sqrt(val) * 0.1 + 10, 2)</code>
        </div>
        <div class="example">
            <strong>Logic:</strong>
            <code>val if val > 0 else 0</code>
        </div>
    </div>

    <!-- Test Harness -->
    <div class="test-harness">
        <div class="test-input-group">
            <label>Test Input (val):</label>
            <input type="number" v-model="testValue" class="test-input">
        </div>
        <button type="button" class="btn btn-secondary btn-sm" @click="testFormula" :disabled="testing">
            <i class="pi pi-play"></i> Test
        </button>
        
        <div class="test-output">
            <span v-if="testing" class="text-muted">Testing...</span>
            <span v-else-if="testResult !== null" class="result-success">Result: {{ testResult }}</span>
            <span v-else-if="testError" class="result-error">Error: {{ testError }}</span>
            <span v-else class="text-muted">No result</span>
        </div>
    </div>

  </div>
</template>

<style scoped>
.formula-editor {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 1rem;
    background: var(--surface-card);
}

/* Toolbox */
.toolbox {
    display: flex;
    flex-wrap: wrap;
    gap: 1rem;
    padding-bottom: 0.75rem;
    border-bottom: 1px solid var(--border);
}

.toolbox-category {
    display: flex;
    align-items: center;
    gap: 0.25rem;
}

.category-label {
    font-size: 0.7rem;
    text-transform: uppercase;
    color: var(--text-muted);
    font-weight: 600;
    margin-right: 0.25rem;
}

.tool-btn {
    background: var(--surface-hover);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 0.25rem 0.5rem;
    font-size: 0.8rem;
    color: var(--text);
    cursor: pointer;
    transition: all 0.2s;
    font-family: monospace;
}

.tool-btn:hover {
    background: var(--primary);
    color: white;
    border-color: var(--primary);
}

.var-btn {
    background: var(--primary-light);
    color: var(--primary);
    border-color: var(--primary-light);
    font-weight: bold;
}

/* Editor */
.editor-container {
    position: relative;
}

.formula-input {
    width: 100%;
    background: var(--input-bg);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 0.75rem;
    color: var(--text);
    font-family: monospace;
    font-size: 1rem;
    line-height: 1.5;
    resize: vertical;
}

.formula-input:focus {
    outline: none;
    border-color: var(--primary);
}

.help-toggle {
    position: absolute;
    top: 0.5rem;
    right: 0.5rem;
    background: none;
    border: none;
    color: var(--text-muted);
    cursor: pointer;
}

.help-toggle:hover {
    color: var(--text);
}

/* Help Box */
.help-box {
    background: var(--surface-hover);
    border-radius: 4px;
    padding: 0.75rem;
    font-size: 0.85rem;
}

.help-box h4 {
    margin: 0 0 0.5rem 0;
    font-size: 0.9rem;
}

.example {
    margin-top: 0.5rem;
    display: flex;
    gap: 0.5rem;
}

.example code {
    background: var(--input-bg);
    padding: 0.1rem 0.3rem;
    border-radius: 3px;
    font-family: monospace;
}

/* Test Harness */
.test-harness {
    display: flex;
    align-items: center;
    gap: 1rem;
    padding-top: 0.75rem;
    border-top: 1px solid var(--border);
}

.test-input-group {
    display: flex;
    align-items: center;
    gap: 0.5rem;
}

.test-input-group label {
    font-size: 0.85rem;
    color: var(--text-muted);
}

.test-input {
    width: 80px;
    padding: 0.25rem 0.5rem;
    background: var(--input-bg);
    border: 1px solid var(--border);
    border-radius: 4px;
    color: var(--text);
}

.btn-sm {
    padding: 0.35rem 0.75rem;
    font-size: 0.85rem;
}

.test-output {
    flex: 1;
    font-family: monospace;
    font-size: 0.9rem;
    display: flex;
    align-items: center;
}

.result-success {
    color: var(--success);
    font-weight: 600;
}

.result-error {
    color: var(--danger);
}
</style>
