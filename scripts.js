document.addEventListener('DOMContentLoaded', () => {
  const loginForm = document.querySelector('#login-form');
  const messageBox = document.querySelector('#form-message');

  if (loginForm) {
    loginForm.addEventListener('submit', (event) => {
      event.preventDefault();
      const email = loginForm.email.value.trim();
      const password = loginForm.password.value;
      const remember = loginForm.remember.checked;

      if (!email || !password || password.length < 8) {
        messageBox.textContent = 'Please provide a valid email and a password with at least 8 characters.';
        messageBox.className = 'message error';
        return;
      }

      messageBox.textContent = 'Signing in securely...';
      messageBox.className = 'message success';

      window.setTimeout(() => {
        const storeKey = remember ? 'occAssistRememberedEmail' : null;
        if (storeKey) {
          localStorage.setItem(storeKey, email);
        }
        window.location.href = 'live-updates.html';
      }, 600);
    });
  }

  const mapContainer = document.querySelector('#map');
  if (mapContainer && typeof mapboxgl !== 'undefined') {
    if (window.MAPBOX_TOKEN && window.MAPBOX_TOKEN !== 'YOUR_MAPBOX_ACCESS_TOKEN_HERE') {
      mapboxgl.accessToken = window.MAPBOX_TOKEN;
      new mapboxgl.Map({
        container: 'map',
        style: 'mapbox://styles/mapbox/dark-v11',
        center: [-0.1276, 51.5074],
        zoom: 10,
      });
    } else {
      mapContainer.innerHTML = '<div class="map-overlay"><p>Mapbox token is not configured. Add your API key to <code>window.MAPBOX_TOKEN</code>.</p></div>';
    }
  }
});
