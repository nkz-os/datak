<script setup lang="ts">
import { ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const navItems = [
  { path: '/dashboard', icon: 'pi-chart-bar', label: 'Dashboard' },
  { path: '/sensors', icon: 'pi-wifi', label: 'Sensors' },
  { path: '/reports', icon: 'pi-file', label: 'Reports' },
  { path: '/config', icon: 'pi-cog', label: 'Configuration' },
  { path: '/report-jobs', icon: 'pi-calendar', label: 'Jobs' },
  { path: '/automation', icon: 'pi-bolt', label: 'Automation' },
]

function handleLogout() {
  auth.logout()
  // Force reload to clear any state and go to login
  window.location.href = '/login'
}

// Password Change Logic
const showPasswordModal = ref(false)
const isChangingPass = ref(false)
const passForm = ref({ current: '', new: '' })

async function handlePasswordChange() {
  isChangingPass.value = true
  try {
    await auth.changePassword(passForm.value.current, passForm.value.new)
    alert('Password changed successfully')
    showPasswordModal.value = false
    passForm.value = { current: '', new: '' }
  } catch (e: any) {
    alert('Failed: ' + (e.response?.data?.detail || e.message))
  } finally {
    isChangingPass.value = false
  }
}
</script>

<template>
  <div class="app-container">
    <aside class="sidebar">
      <div class="sidebar-header">
        <div class="sidebar-logo-container">
          <img src="/logo.png" alt="Logo" class="sidebar-logo-img" />
        </div>
        <span class="sidebar-title">DaTaK</span>
      </div>

      <nav>
        <ul class="nav-menu">
          <li v-for="item in navItems" :key="item.path">
            <RouterLink 
              :to="item.path" 
              class="nav-item"
              :class="{ active: route.path === item.path }"
            >
              <i :class="['pi', item.icon]"></i>
              <span>{{ item.label }}</span>
            </RouterLink>
          </li>
        </ul>
      </nav>

      <div style="margin-top: auto; padding-top: 1rem; border-top: 1px solid var(--border);">
        <div class="user-profile" style="margin-bottom: 1rem; padding: 0 1rem;">
          <small style="color: var(--text-muted); display: block; margin-bottom: 0.25rem;">Logged in as</small>
          <div style="font-weight: 500; display: flex; align-items: center; gap: 0.5rem;">
            <i class="pi pi-user" style="background: var(--surface-hover); padding: 0.4rem; border-radius: 50%;"></i>
            <span>{{ auth.user?.username }}</span>
          </div>
        </div>
        
        <button class="nav-item action-btn" @click="showPasswordModal = true">
          <i class="pi pi-key"></i>
          <span>Change Password</span>
        </button>
        
        <button class="nav-item action-btn error-hover" @click="handleLogout">
          <i class="pi pi-sign-out"></i>
          <span>Sign Out</span>
        </button>
      </div>
    </aside>

    <!-- Change Password Modal -->
    <div v-if="showPasswordModal" class="modal-overlay" @click.self="showPasswordModal = false">
      <div class="modal-content" style="max-width: 400px;">
        <div class="modal-header">
          <h2>Change Password</h2>
          <button class="btn-close" @click="showPasswordModal = false"><i class="pi pi-times"></i></button>
        </div>
        <form @submit.prevent="handlePasswordChange" class="modal-body">
          <div class="form-group">
            <label class="form-label">Current Password</label>
            <input v-model="passForm.current" type="password" class="form-input" required />
          </div>
          <div class="form-group">
             <label class="form-label">New Password</label>
             <input v-model="passForm.new" type="password" class="form-input" required minlength="8" />
             <small class="text-muted">Min 8 characters</small>
          </div>
          <div class="modal-footer">
            <button type="button" class="btn btn-secondary" @click="showPasswordModal = false">Cancel</button>
            <button type="submit" class="btn btn-primary" :disabled="isChangingPass">
              <i v-if="isChangingPass" class="pi pi-spin pi-spinner"></i>
              {{ isChangingPass ? 'Updating...' : 'Update Password' }}
            </button>
          </div>
        </form>
      </div>
    </div>

    <main class="main-content">
      <slot />
    </main>
  </div>
</template>

<style scoped>
.app-container {
  display: flex;
  height: 100vh;
  width: 100vw;
  background-color: var(--background);
  color: var(--text);
}

.sidebar {
  width: 260px;
  background-color: var(--surface);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  padding: 1.5rem;
  flex-shrink: 0;
}

.sidebar-header {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin-bottom: 2rem;
  padding-bottom: 1rem;
  border-bottom: 1px solid var(--border);
}

.sidebar-logo-container {
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.sidebar-logo-img {
  width: 100%;
  height: 100%;
  object-fit: contain;
  border-radius: 4px;
}

.sidebar-title {
  font-size: 1.25rem;
  font-weight: 700;
  letter-spacing: -0.025em;
}

.nav-menu {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  flex: 1;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.75rem 1rem;
  color: var(--text-muted);
  text-decoration: none;
  border-radius: 8px;
  transition: all 0.2s ease;
  cursor: pointer;
  border: none;
  background: none;
  width: 100%;
  font-size: 0.95rem;
  font-family: inherit;
  text-align: left;
}

.nav-item:hover {
  color: var(--text);
  background: var(--surface-hover);
}

.nav-item.active {
  color: var(--primary);
  background: rgba(37, 99, 235, 0.1);
  font-weight: 500;
}

.action-btn {
  margin-top: 0.25rem;
}

.error-hover:hover {
  color: #ef4444 !important;
  background: rgba(239, 68, 68, 0.1) !important;
}

.main-content {
  flex: 1;
  overflow-y: auto;
  padding: 2rem;
}

/* Modal Styles */
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.7);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.modal-content {
  background: var(--surface);
  border-radius: 16px;
  border: 1px solid var(--border);
  width: 90%;
  max-width: 600px;
  max-height: 90vh;
  overflow-y: auto;
}

.modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 1.25rem 1.5rem;
  border-bottom: 1px solid var(--border);
}

.modal-header h2 {
  font-size: 1.25rem;
  font-weight: 600;
}

.btn-close {
  background: none;
  border: none;
  color: var(--text-muted);
  cursor: pointer;
  padding: 0.5rem;
}

.modal-body {
  padding: 1.5rem;
}

.modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 0.75rem;
  margin-top: 1.5rem;
  padding-top: 1rem;
  border-top: 1px solid var(--border);
}

.form-group {
    margin-bottom: 1rem;
}

.form-label {
    display: block;
    margin-bottom: 0.5rem;
    font-weight: 500;
    font-size: 0.875rem;
}

.form-input {
    width: 100%;
    padding: 0.625rem 0.875rem;
    background: var(--background);
    border: 1px solid var(--border);
    border-radius: 8px;
    color: var(--text);
    font-size: 0.95rem;
}

.form-input:focus {
    outline: none;
    border-color: var(--primary);
    box-shadow: 0 0 0 2px rgba(37, 99, 235, 0.2);
}
</style>
