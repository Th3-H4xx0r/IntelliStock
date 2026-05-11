<script setup>
import { RouterLink } from 'vue-router'
</script>

<template>
  <div class="layout">
    <header class="navbar">
      <RouterLink to="/" class="brand">IntelliStock</RouterLink>
      <nav class="nav-links">
        <RouterLink to="/">Home</RouterLink>
        <!-- Add more nav links here as you add pages -->
      </nav>
    </header>

    <main class="page-content">
      <slot />
    </main>

    <footer class="footer">
      <span>© 2026 IntelliStock</span>
    </footer>
  </div>
</template>

<style scoped>
.layout {
  display: flex;
  flex-direction: column;
  min-height: 100vh;
}

.navbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 2rem;
  height: 60px;
  background: #0f1117;
  border-bottom: 1px solid #1e2130;
}

.brand {
  font-size: 1.25rem;
  font-weight: 700;
  color: #4ade80;
  text-decoration: none;
  letter-spacing: 0.5px;
}

.nav-links {
  display: flex;
  gap: 1.5rem;
}

.nav-links a {
  color: #a1a1aa;
  text-decoration: none;
  font-size: 0.9rem;
  transition: color 0.2s;
}

.nav-links a:hover,
.nav-links a.router-link-active {
  color: #fff;
}

.page-content {
  flex: 1;
}

.footer {
  text-align: center;
  padding: 1rem;
  font-size: 0.8rem;
  color: #52525b;
  border-top: 1px solid #1e2130;
}
</style>
