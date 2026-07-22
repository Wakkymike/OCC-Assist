document.addEventListener('DOMContentLoaded', () => {
  const rememberedEmail = localStorage.getItem('occAssistRememberedEmail');
  const loginForm = document.querySelector('#login-form');
  const messageBox = document.querySelector('#form-message');

  if (loginForm) {
    if (rememberedEmail && loginForm.email) {
      loginForm.email.value = rememberedEmail;
      if (loginForm.remember) {
        loginForm.remember.checked = true;
      }
    }

    loginForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      const email = loginForm.email.value.trim();
      const password = loginForm.password.value;
      const remember = loginForm.remember.checked;

      if (!email || !password || password.length < 8) {
        setMessage(messageBox, 'Please provide a valid email and a password with at least 8 characters.', 'error');
        return;
      }

      setMessage(messageBox, 'Signing in securely...', 'success');

      const response = await fetch(loginForm.dataset.loginUrl, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ email, password }),
      });
      const payload = await response.json();

      if (!response.ok) {
        setMessage(messageBox, payload.message || 'Unable to sign in.', 'error');
        return;
      }

      if (remember) {
        localStorage.setItem('occAssistRememberedEmail', email);
      } else {
        localStorage.removeItem('occAssistRememberedEmail');
      }

      window.location.href = payload.redirect;
    });
  }

  const logoutButton = document.querySelector('[data-action="logout"]');
  if (logoutButton) {
    logoutButton.addEventListener('click', async () => {
      await fetch(window.OCC_ASSIST.logoutUrl, { method: 'POST' });
      window.location.href = '/';
    });
  }

  initializeUsersPage();
  initializeMap();
});

function setMessage(element, message, variant = '') {
  if (!element) {
    return;
  }

  element.textContent = message;
  element.className = variant ? `message ${variant}` : 'message';
}

function initializeMap() {
  const mapContainer = document.querySelector('#map');
  const mapStatus = document.querySelector('#map-status');
  if (!mapContainer || typeof mapboxgl === 'undefined') {
    return;
  }

  if (window.MAPBOX_TOKEN && window.MAPBOX_TOKEN !== 'YOUR_MAPBOX_ACCESS_TOKEN_HERE') {
    mapboxgl.accessToken = window.MAPBOX_TOKEN;
    const map = new mapboxgl.Map({
      container: 'map',
      style: 'mapbox://styles/mapbox/dark-v11',
      center: [-0.1276, 51.5074],
      zoom: 10,
    });

    const markers = new Map();
    let hasFittedVehicles = false;

    const renderVehicles = (vehicles) => {
      const activeIds = new Set();
      const bounds = new mapboxgl.LngLatBounds();

      vehicles.forEach((vehicle) => {
        activeIds.add(vehicle.id);
        const lngLat = [vehicle.longitude, vehicle.latitude];
        bounds.extend(lngLat);

        const labelText = [
          `Service ${vehicle.service}`,
          vehicle.destination,
          vehicle.direction,
          `Fleet ${vehicle.fleetNumber}`,
        ].join(' | ');

        if (markers.has(vehicle.id)) {
          const markerState = markers.get(vehicle.id);
          markerState.marker.setLngLat(lngLat);
          markerState.flag.textContent = labelText;
          markerState.flag.dataset.direction = vehicle.direction;
          return;
        }

        const markerElement = document.createElement('div');
        markerElement.className = 'vehicle-marker';

        const flag = document.createElement('div');
        flag.className = 'vehicle-flag';
        flag.dataset.direction = vehicle.direction;
        flag.textContent = labelText;

        const pin = document.createElement('div');
        pin.className = 'vehicle-pin';
        pin.textContent = vehicle.service;

        markerElement.append(flag, pin);

        const marker = new mapboxgl.Marker({ element: markerElement, anchor: 'bottom' })
          .setLngLat(lngLat)
          .addTo(map);

        markers.set(vehicle.id, { marker, flag });
      });

      markers.forEach((markerState, markerId) => {
        if (!activeIds.has(markerId)) {
          markerState.marker.remove();
          markers.delete(markerId);
        }
      });

      if (!hasFittedVehicles && vehicles.length > 0) {
        map.fitBounds(bounds, { padding: 72, maxZoom: 12, duration: 0 });
        hasFittedVehicles = true;
      }
    };

    const refreshVehicles = async () => {
      if (!window.OCC_ASSIST.trackingVehiclesUrl) {
        return;
      }

      try {
        const response = await fetch(window.OCC_ASSIST.trackingVehiclesUrl, { cache: 'no-store' });
        const payload = await response.json();
        if (!response.ok || !payload.ok) {
          throw new Error(payload.message || 'Unable to load vehicle positions.');
        }

        renderVehicles(payload.vehicles || []);
        const updated = formatFeedTime(payload.sourceTimestamp || payload.refreshedAt);
        setMessage(mapStatus, `${payload.vehicles.length} vehicle${payload.vehicles.length === 1 ? '' : 's'} updated ${updated}.`, 'success');
      } catch (error) {
        setMessage(mapStatus, error.message || 'Unable to load vehicle positions.', 'error');
      }
    };

    map.on('load', () => {
      refreshVehicles();
      window.setInterval(refreshVehicles, 7000);
    });
    return;
  }

  mapContainer.innerHTML = '<div class="placeholder-card"><p>Mapbox token is not configured yet.</p></div>';
}

function formatFeedTime(value) {
  if (!value) {
    return 'just now';
  }

  const timestamp = new Date(value);
  if (Number.isNaN(timestamp.getTime())) {
    return 'just now';
  }

  return timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function initializeUsersPage() {
  const userForm = document.querySelector('#user-form');
  const usersList = document.querySelector('#users-list');
  const refreshButton = document.querySelector('#refresh-users');
  const usersMessage = document.querySelector('#users-message');
  const formMessage = document.querySelector('#user-form-message');

  if (!userForm || !usersList) {
    return;
  }

  const loadUsers = async () => {
    setMessage(usersMessage, 'Loading users...');
    const response = await fetch(window.OCC_ASSIST.usersApiUrl);
    const payload = await response.json();

    if (!response.ok) {
      setMessage(usersMessage, payload.message || 'Unable to load users.', 'error');
      return;
    }

    renderUsers(usersList, payload.users, payload.permissionLabels);
    setMessage(usersMessage, `${payload.users.length} user${payload.users.length === 1 ? '' : 's'} loaded.`, 'success');
  };

  userForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    const email = userForm.email.value.trim();
    const password = userForm.password.value;
    const permissions = {};
    userForm.querySelectorAll('input[name="permission"]').forEach((input) => {
      permissions[input.value] = input.checked;
    });

    const response = await fetch(window.OCC_ASSIST.createUserUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ email, password, permissions }),
    });
    const payload = await response.json();

    if (!response.ok) {
      setMessage(formMessage, payload.message || 'Unable to create user.', 'error');
      return;
    }

    userForm.reset();
    setMessage(formMessage, 'User created successfully.', 'success');
    await loadUsers();
  });

  usersList.addEventListener('change', async (event) => {
    const toggle = event.target;
    if (!toggle.matches('[data-permission-key]')) {
      return;
    }

    const userId = toggle.dataset.userId;
    const permissionKey = toggle.dataset.permissionKey;
    const enabled = toggle.checked;

    const response = await fetch(`${window.OCC_ASSIST.permissionsBaseUrl}/${userId}/permissions`, {
      method: 'PATCH',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ permissionKey, enabled }),
    });
    const payload = await response.json();

    if (!response.ok) {
      toggle.checked = !enabled;
      setMessage(usersMessage, payload.message || 'Unable to update permission.', 'error');
      return;
    }

    setMessage(usersMessage, 'Permission updated.', 'success');
  });

  refreshButton.addEventListener('click', () => {
    loadUsers();
  });

  loadUsers();
}

function renderUsers(container, users, permissionLabels) {
  container.innerHTML = users
    .map((user) => {
      const permissionMarkup = Object.entries(permissionLabels)
        .map(([permissionKey, label]) => {
          const isLocked = user.isSuperadmin;
          return `
            <label class="permission-toggle">
              <span>${label}</span>
              <span class="toggle">
                <input
                  type="checkbox"
                  data-user-id="${user.id}"
                  data-permission-key="${permissionKey}"
                  ${user.permissions[permissionKey] ? 'checked' : ''}
                  ${isLocked ? 'disabled' : ''}
                />
                <span></span>
              </span>
            </label>
          `;
        })
        .join('');

      return `
        <article class="user-card">
          <div class="user-card-head">
            <div>
              <h3>${user.email}</h3>
              <p class="user-meta">Created ${user.createdAt}</p>
            </div>
            <span class="badge">${user.isSuperadmin ? 'Superadmin' : 'Standard user'}</span>
          </div>
          <div class="permission-list">${permissionMarkup}</div>
        </article>
      `;
    })
    .join('');
}