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
  initializeDrivingHours();
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
  const boltonCenter = [-2.428219, 53.576864];
  const staleAfterMs = 120_000;
  if (!mapContainer || typeof mapboxgl === 'undefined') {
    return;
  }

  if (window.MAPBOX_TOKEN && window.MAPBOX_TOKEN !== 'YOUR_MAPBOX_ACCESS_TOKEN_HERE') {
    mapboxgl.accessToken = window.MAPBOX_TOKEN;
    const map = new mapboxgl.Map({
      container: 'map',
      style: 'mapbox://styles/mapbox/dark-v11',
      center: boltonCenter,
      zoom: 12,
    });

    const vehicleStates = new Map();
    let refreshIntervalId = null;

    const buildVehicleSignature = (vehicle) => {
      return [
        vehicle.recordedAt,
        vehicle.latitude,
        vehicle.longitude,
        vehicle.service,
        vehicle.destination,
        vehicle.direction,
        vehicle.fleetNumber,
      ].join('|');
    };

    const removeVehicleMarker = (state) => {
      if (!state.marker) {
        return;
      }

      state.marker.remove();
      state.marker = null;
      state.flag = null;
    };

    const ensureVehicleMarker = (state, lngLat, labelText, direction, service) => {
      if (state.marker) {
        state.marker.setLngLat(lngLat);
        state.flag.textContent = labelText;
        state.flag.dataset.direction = direction;
        return;
      }

      const markerElement = document.createElement('div');
      markerElement.className = 'vehicle-marker';

      const flag = document.createElement('div');
      flag.className = 'vehicle-flag';
      flag.dataset.direction = direction;
      flag.textContent = labelText;

      const pin = document.createElement('div');
      pin.className = 'vehicle-pin';
      pin.textContent = service;

      markerElement.append(flag, pin);

      state.flag = flag;
      state.marker = new mapboxgl.Marker({ element: markerElement, anchor: 'bottom' })
        .setLngLat(lngLat)
        .addTo(map);
    };

    const applyZoomStyling = () => {
      const zoom = map.getZoom();
      const normalized = Math.max(0, Math.min(1, (zoom - 12) / 4));
      const flagScale = 0.3 + normalized * 0.7;
      const flagOpacity = 0.18 + normalized * 0.82;
      mapContainer.style.setProperty('--vehicle-flag-scale', flagScale.toFixed(2));
      mapContainer.style.setProperty('--vehicle-flag-opacity', flagOpacity.toFixed(2));
    };

    const renderVehicles = (vehicles, observedAtMs) => {
      const activeIds = new Set();
      let visibleVehicleCount = 0;

      vehicles.forEach((vehicle) => {
        activeIds.add(vehicle.id);
        const lngLat = [vehicle.longitude, vehicle.latitude];
        const signature = buildVehicleSignature(vehicle);

        const labelText = [
          `Service ${vehicle.service}`,
          vehicle.destination,
          vehicle.direction,
          `Fleet ${vehicle.fleetNumber}`,
        ].join(' | ');

        if (vehicleStates.has(vehicle.id)) {
          const vehicleState = vehicleStates.get(vehicle.id);
          if (vehicleState.lastSignature !== signature) {
            vehicleState.lastSignature = signature;
            vehicleState.lastFreshAtMs = observedAtMs;
          }

          if (observedAtMs - vehicleState.lastFreshAtMs > staleAfterMs) {
            removeVehicleMarker(vehicleState);
            return;
          }

          ensureVehicleMarker(vehicleState, lngLat, labelText, vehicle.direction, vehicle.service);
          visibleVehicleCount += 1;
          return;
        }

        const vehicleState = {
          marker: null,
          flag: null,
          lastFreshAtMs: observedAtMs,
          lastSignature: signature,
        };
        ensureVehicleMarker(vehicleState, lngLat, labelText, vehicle.direction, vehicle.service);
        vehicleStates.set(vehicle.id, vehicleState);
        visibleVehicleCount += 1;
      });

      vehicleStates.forEach((vehicleState, vehicleId) => {
        if (!activeIds.has(vehicleId)) {
          removeVehicleMarker(vehicleState);
          vehicleStates.delete(vehicleId);
        }
      });

      return visibleVehicleCount;
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

        const observedAtMs = Date.parse(payload.sourceTimestamp || payload.refreshedAt) || Date.now();
        const activeVehicles = payload.vehicles || [];

        const visibleVehicleCount = renderVehicles(activeVehicles, observedAtMs);
        const updated = formatFeedTime(payload.sourceTimestamp || payload.refreshedAt);
        setMessage(mapStatus, `${visibleVehicleCount} live vehicle${visibleVehicleCount === 1 ? '' : 's'} updated ${updated}.`, 'success');
      } catch (error) {
        setMessage(mapStatus, error.message || 'Unable to load vehicle positions.', 'error');
      }
    };

    const startVehicleRefresh = () => {
      if (refreshIntervalId !== null) {
        return;
      }

      setMessage(mapStatus, 'Loading vehicle positions...', 'success');
      refreshVehicles();
      refreshIntervalId = window.setInterval(refreshVehicles, 7000);
    };

    if (map.loaded()) {
      startVehicleRefresh();
    } else {
      map.once('load', startVehicleRefresh);
      window.setTimeout(startVehicleRefresh, 1500);
    }

    applyZoomStyling();
    map.on('zoom', applyZoomStyling);

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

function initializeDrivingHours() {
  const segmentForm = document.querySelector('#segment-form');
  const segmentList = document.querySelector('#segment-list');
  const clearButton = document.querySelector('#clear-segments');
  const formMessage = document.querySelector('#segment-message');
  const metricsPanel = document.querySelector('#hours-metrics');
  const alertsPanel = document.querySelector('#hours-alerts');

  if (!segmentForm || !segmentList || !clearButton || !metricsPanel || !alertsPanel) {
    return;
  }

  const minutesPerHour = 60;
  const limits = {
    dailyDrivingMinutes: 10 * minutesPerHour,
    spreadoverMinutes: 16 * minutesPerHour,
    breakTriggerDrivingMinutes: 5.5 * minutesPerHour,
    shortBreakMinutes: 30,
    longDayThresholdMinutes: 8.5 * minutesPerHour,
    longDayNonDrivingMinutes: 45,
  };

  let segments = [];

  const parseTimeToMinutes = (value) => {
    const [hour, minute] = value.split(':').map((item) => Number(item));
    return hour * minutesPerHour + minute;
  };

  const formatMinutesAsTime = (totalMinutes) => {
    const normalized = ((totalMinutes % 1440) + 1440) % 1440;
    const hour = String(Math.floor(normalized / 60)).padStart(2, '0');
    const minute = String(normalized % 60).padStart(2, '0');
    return `${hour}:${minute}`;
  };

  const formatDuration = (minutes) => {
    const safeMinutes = Math.max(0, Math.round(minutes));
    const hours = Math.floor(safeMinutes / 60);
    const remainingMinutes = safeMinutes % 60;
    return `${hours}h ${String(remainingMinutes).padStart(2, '0')}m`;
  };

  const getOverlapMinutes = (segmentStart, segmentEnd, windowStart, windowEnd) => {
    const start = Math.max(segmentStart, windowStart);
    const end = Math.min(segmentEnd, windowEnd);
    return Math.max(0, end - start);
  };

  const sortSegments = () => {
    segments.sort((left, right) => left.startMinutes - right.startMinutes);
  };

  const hasOverlap = (startMinutes, endMinutes) => {
    return segments.some((segment) => !(endMinutes <= segment.startMinutes || startMinutes >= segment.endMinutes));
  };

  const getContinuousDrivingAtEnd = (orderedSegments) => {
    let continuous = 0;
    for (let index = orderedSegments.length - 1; index >= 0; index -= 1) {
      const segment = orderedSegments[index];
      const duration = segment.endMinutes - segment.startMinutes;
      if (segment.type === 'driving') {
        continuous += duration;
        continue;
      }
      if (duration >= limits.shortBreakMinutes) {
        break;
      }
    }
    return continuous;
  };

  const calculateCompliance = (orderedSegments) => {
    if (orderedSegments.length === 0) {
      return {
        totalDrivingMinutes: 0,
        totalBreakMinutes: 0,
        spreadoverMinutes: 0,
        currentContinuousDrivingMinutes: 0,
        nonDrivingInFirstWindowMinutes: 0,
        breaches: [],
      };
    }

    const dayStart = orderedSegments[0].startMinutes;
    const dayEnd = orderedSegments[orderedSegments.length - 1].endMinutes;
    const spreadoverMinutes = dayEnd - dayStart;

    let totalDrivingMinutes = 0;
    let totalBreakMinutes = 0;
    let currentSpellDriving = 0;
    let breakRuleAExceeded = false;
    let nonDrivingInFirstWindowMinutes = 0;
    let hasBreak30AfterLongWindow = false;
    const longDayWindowEnd = dayStart + limits.longDayThresholdMinutes;

    orderedSegments.forEach((segment) => {
      const duration = segment.endMinutes - segment.startMinutes;
      if (segment.type === 'driving') {
        totalDrivingMinutes += duration;
        currentSpellDriving += duration;
        if (currentSpellDriving > limits.breakTriggerDrivingMinutes) {
          breakRuleAExceeded = true;
        }
        return;
      }

      totalBreakMinutes += duration;
      nonDrivingInFirstWindowMinutes += getOverlapMinutes(
        segment.startMinutes,
        segment.endMinutes,
        dayStart,
        longDayWindowEnd,
      );

      if (duration >= limits.shortBreakMinutes && segment.startMinutes >= longDayWindowEnd) {
        hasBreak30AfterLongWindow = true;
      }

      if (duration >= limits.shortBreakMinutes) {
        currentSpellDriving = 0;
      }
    });

    const currentContinuousDrivingMinutes = getContinuousDrivingAtEnd(orderedSegments);
    const breaches = [];

    if (totalDrivingMinutes > limits.dailyDrivingMinutes) {
      breaches.push(`Daily driving limit exceeded: ${formatDuration(totalDrivingMinutes)} (limit ${formatDuration(limits.dailyDrivingMinutes)}).`);
    }

    if (spreadoverMinutes > limits.spreadoverMinutes) {
      breaches.push(`Spreadover limit exceeded: ${formatDuration(spreadoverMinutes)} (limit ${formatDuration(limits.spreadoverMinutes)}).`);
    }

    if (spreadoverMinutes < limits.longDayThresholdMinutes) {
      if (breakRuleAExceeded) {
        breaches.push('Break breach: a 30-minute break is required before driving exceeds 5h 30m.');
      }
    } else {
      const optionA = !breakRuleAExceeded;
      const optionB =
        nonDrivingInFirstWindowMinutes >= limits.longDayNonDrivingMinutes && hasBreak30AfterLongWindow;

      if (!optionA && !optionB) {
        breaches.push('Break breach: for days of 8h 30m or more, either take a 30-minute break before 5h 30m driving, or complete 45 minutes non-driving in first 8h 30m and then take a 30-minute break.');
      }
    }

    return {
      totalDrivingMinutes,
      totalBreakMinutes,
      spreadoverMinutes,
      currentContinuousDrivingMinutes,
      nonDrivingInFirstWindowMinutes,
      breaches,
    };
  };

  const renderMetrics = (summary) => {
    const metrics = [
      ['Current Driving Before Break', formatDuration(summary.currentContinuousDrivingMinutes)],
      ['Total Driving Today', formatDuration(summary.totalDrivingMinutes)],
      ['Total Break Time', formatDuration(summary.totalBreakMinutes)],
      ['Spreadover', formatDuration(summary.spreadoverMinutes)],
      ['Non-Driving In First 8h 30m', formatDuration(summary.nonDrivingInFirstWindowMinutes)],
    ];

    metricsPanel.innerHTML = metrics
      .map(
        ([label, value]) => `
          <article class="hours-metric">
            <p>${label}</p>
            <strong>${value}</strong>
          </article>
        `,
      )
      .join('');
  };

  const renderAlerts = (summary) => {
    if (summary.breaches.length === 0) {
      alertsPanel.innerHTML = '<div class="hours-alert ok">No GB domestic breaches detected in the current timeline.</div>';
      return;
    }

    alertsPanel.innerHTML = summary.breaches
      .map((message) => `<div class="hours-alert breach">${message}</div>`)
      .join('');
  };

  const renderSegments = () => {
    sortSegments();

    if (segments.length === 0) {
      segmentList.innerHTML = '<tr><td colspan="5" class="hours-empty">No segments logged yet.</td></tr>';
      renderMetrics(calculateCompliance([]));
      renderAlerts(calculateCompliance([]));
      return;
    }

    segmentList.innerHTML = segments
      .map((segment) => {
        const duration = segment.endMinutes - segment.startMinutes;
        return `
          <tr>
            <td>${segment.type === 'driving' ? 'Driving' : 'Break'}</td>
            <td>${formatMinutesAsTime(segment.startMinutes)}</td>
            <td>${formatMinutesAsTime(segment.endMinutes)}</td>
            <td>${formatDuration(duration)}</td>
            <td><button class="btn secondary compact" type="button" data-remove-segment="${segment.id}">Remove</button></td>
          </tr>
        `;
      })
      .join('');

    const summary = calculateCompliance(segments);
    renderMetrics(summary);
    renderAlerts(summary);
  };

  segmentForm.addEventListener('submit', (event) => {
    event.preventDefault();
    const type = String(segmentForm.type.value || '').trim();
    const start = String(segmentForm.start.value || '').trim();
    const end = String(segmentForm.end.value || '').trim();

    if (!type || !start || !end) {
      setMessage(formMessage, 'Select a segment type and both times before adding.', 'error');
      return;
    }

    const startMinutes = parseTimeToMinutes(start);
    const endMinutes = parseTimeToMinutes(end);

    if (endMinutes <= startMinutes) {
      setMessage(formMessage, 'End time must be after start time on the same day.', 'error');
      return;
    }

    if (hasOverlap(startMinutes, endMinutes)) {
      setMessage(formMessage, 'This segment overlaps an existing segment. Remove or adjust the overlap first.', 'error');
      return;
    }

    segments.push({
      id: `${Date.now()}-${Math.random().toString(16).slice(2, 8)}`,
      type,
      startMinutes,
      endMinutes,
    });

    renderSegments();
    setMessage(formMessage, 'Segment added.', 'success');
    segmentForm.reset();
  });

  segmentList.addEventListener('click', (event) => {
    const trigger = event.target;
    if (!(trigger instanceof HTMLElement)) {
      return;
    }

    const removeId = trigger.getAttribute('data-remove-segment');
    if (!removeId) {
      return;
    }

    segments = segments.filter((segment) => segment.id !== removeId);
    renderSegments();
    setMessage(formMessage, 'Segment removed.', 'success');
  });

  clearButton.addEventListener('click', () => {
    segments = [];
    renderSegments();
    setMessage(formMessage, 'All segments cleared.', 'success');
  });

  renderSegments();
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