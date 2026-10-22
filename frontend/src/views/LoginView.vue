<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const router = useRouter()
const auth = useAuthStore()

const username = ref('')
const password = ref('')
const loading = ref(false)
const error = ref('')

async function handleLogin() {
  if (!username.value || !password.value) {
    error.value = 'Please enter username and password'
    return
  }

  loading.value = true
  error.value = ''

  const success = await auth.login(username.value, password.value)
  
  loading.value = false

  if (success) {
    router.push('/dashboard')
  } else {
    error.value = 'Invalid username or password'
  }
}
</script>

<template>
  <div class="login-container">
    <div class="login-card">
      <div class="login-header">
        <div class="login-logo-container">
          <img src="/logo.png" alt="Logo" class="login-logo-img" />
        </div>
        <h1>DaTaK Gateway</h1>
        <p>Industrial IoT Edge Gateway</p>
        <div class="powered-by">
          <span>by</span>
          <img src="/logo_robotika.svg" alt="Robotika" class="robotika-logo" />
        </div>
      </div>

      <form @submit.prevent="handleLogin" class="login-form">
        <div class="form-group">
          <label class="form-label">Username</label>
          <input 
            v-model="username"
            type="text" 
            class="form-input" 
            placeholder="Enter username"
            autocomplete="username"
          />
        </div>

        <div class="form-group">
          <label class="form-label">Password</label>
          <input 
            v-model="password"
            type="password" 
            class="form-input" 
            placeholder="Enter password"
            autocomplete="current-password"
          />
        </div>

        <div v-if="error" class="error-message">
          <i class="pi pi-exclamation-circle"></i>
          {{ error }}
        </div>

        <button type="submit" class="btn btn-primary login-btn" :disabled="loading">
          <i v-if="loading" class="pi pi-spin pi-spinner"></i>
          <span v-else>Sign In</span>
        </button>
      </form>

      <div class="login-footer">
        <p style="margin-bottom: 0.5rem;">Default: admin / admin</p>
        <div class="contact-info">
          <a href="mailto:kate@example.com" class="contact-link">kate@example.com</a>
          <span class="separator">•</span>
          <span class="contact-link">IoT Solutions</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.login-container {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
  padding: 1rem;
}

.login-card {
  background: var(--surface);
  border-radius: 16px;
  border: 1px solid var(--border);
  padding: 2.5rem;
  width: 100%;
  max-width: 400px;
  box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5);
}

.login-header {
  text-align: center;
  margin-bottom: 2rem;
}

.login-logo-container {
  width: 80px;
  height: 80px;
  margin: 0 auto 1rem;
  display: flex;
  align-items: center;
  justify-content: center;
}

.login-logo-img {
  width: 100%;
  height: 100%;
  object-fit: contain;
}

.login-header h1 {
  font-size: 1.5rem;
  font-weight: 700;
  margin-bottom: 0.25rem;
}

.login-header p {
  color: var(--text-muted);
  font-size: 0.875rem;
}

.powered-by {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  margin-top: 1rem;
  color: var(--text-muted);
  font-size: 0.8rem;
}

.robotika-logo {
  height: 24px;
  opacity: 0.8;
  transition: opacity 0.2s;
}

.robotika-logo:hover {
  opacity: 1;
}

.login-form {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.login-btn {
  width: 100%;
  padding: 0.875rem;
  font-size: 1rem;
  margin-top: 0.5rem;
}

.error-message {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  color: var(--error);
  font-size: 0.875rem;
  padding: 0.75rem;
  background: rgba(239, 68, 68, 0.1);
  border-radius: 8px;
}

.login-footer {
  text-align: center;
  margin-top: 1.5rem;
  color: var(--text-muted);
  font-size: 0.75rem;
}

.contact-info {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  margin-top: 0.5rem;
}

.contact-link {
  color: var(--primary);
  text-decoration: none;
  font-weight: 500;
  transition: color 0.2s;
}

.contact-link:hover {
  color: var(--primary-hover, #3b82f6);
  text-decoration: underline;
}

.separator {
  color: var(--border);
}
</style>
