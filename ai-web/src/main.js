const bootstrapModule = window.location.pathname === '/login'
  ? import('./loginBootstrap.js')
  : import('./appBootstrap.js')

bootstrapModule
  .then(({ mount }) => mount('#app'))
  .catch((error) => {
    console.error('Failed to start the frontend', error)
  })
