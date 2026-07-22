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
  initializeDailyOverview();
  initializeSettingsPage();
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
  const routeToggle = document.querySelector('#static-routes-toggle');
  const routeSelect = document.querySelector('#static-route-select');
  const routeStatus = document.querySelector('#map-route-status');
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
    const routeSourceId = 'gnw-route-overlay-source';
    const routeOutlineLayerId = 'gnw-route-overlay-outline';
    const routeLayerId = 'gnw-route-overlay';
    const emptyRouteFeatureCollection = {
      type: 'FeatureCollection',
      features: [],
    };

    const escapeHtml = (value) => String(value || '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#39;');

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

    const setRouteStatus = (message) => {
      if (!routeStatus) {
        return;
      }
      routeStatus.textContent = message;
    };

    const setRouteControlsEnabled = (enabled) => {
      if (routeSelect) {
        routeSelect.disabled = !enabled;
      }
    };

    const ensureRouteOverlayLayers = () => {
      if (map.getSource(routeSourceId)) {
        return;
      }

      map.addSource(routeSourceId, {
        type: 'geojson',
        data: emptyRouteFeatureCollection,
      });

      map.addLayer({
        id: routeOutlineLayerId,
        type: 'line',
        source: routeSourceId,
        paint: {
          'line-color': '#07121b',
          'line-width': 8,
          'line-opacity': 0.88,
        },
      });

      map.addLayer({
        id: routeLayerId,
        type: 'line',
        source: routeSourceId,
        paint: {
          'line-color': '#35deff',
          'line-width': 4,
          'line-opacity': 0.95,
        },
      });
    };

    const applyRouteOverlay = (featureCollection, showOverlay) => {
      if (!map.isStyleLoaded()) {
        return;
      }

      ensureRouteOverlayLayers();
      const source = map.getSource(routeSourceId);
      if (!source) {
        return;
      }

      source.setData(showOverlay ? featureCollection : emptyRouteFeatureCollection);

      const visibility = showOverlay ? 'visible' : 'none';
      if (map.getLayer(routeOutlineLayerId)) {
        map.setLayoutProperty(routeOutlineLayerId, 'visibility', visibility);
      }
      if (map.getLayer(routeLayerId)) {
        map.setLayoutProperty(routeLayerId, 'visibility', visibility);
      }
    };

    const updateRouteOptions = (routes, selectedRoute) => {
      if (!routeSelect) {
        return;
      }

      const currentSelection = selectedRoute || routeSelect.value || 'all';
      routeSelect.innerHTML = [
        '<option value="all">All uploaded routes</option>',
        ...routes.map((route) => {
          const routeId = escapeHtml(route.id || '');
          const routeLabel = escapeHtml(route.label || route.lineName || route.id || 'Route');
          return `<option value="${routeId}">${routeLabel}</option>`;
        }),
      ].join('');

      const routeIds = routes.map((route) => String(route.id || ''));
      const canKeepSelection = currentSelection === 'all' || routeIds.includes(currentSelection);
      routeSelect.value = canKeepSelection ? currentSelection : 'all';
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

    const loadStaticRoutes = async () => {
      if (!window.OCC_ASSIST.trackingStaticRoutesUrl) {
        setRouteStatus('Static route API is not configured.');
        return;
      }

      try {
        const selectedRoute = routeSelect?.value || 'all';
        const query = new URLSearchParams({ route: selectedRoute });
        const response = await fetch(`${window.OCC_ASSIST.trackingStaticRoutesUrl}?${query.toString()}`, { cache: 'no-store' });
        const payload = await response.json();
        if (!response.ok || !payload.ok) {
          throw new Error(payload.message || 'Unable to load static route data.');
        }

        const routes = payload.routes || [];
        updateRouteOptions(routes, payload.selectedRoute || 'all');

        const overlayVisible = Boolean(routeToggle?.checked) && payload.configured;
        applyRouteOverlay(payload.featureCollection || emptyRouteFeatureCollection, overlayVisible);
        setRouteControlsEnabled(Boolean(routeToggle?.checked) && payload.configured);

        if (!payload.configured) {
          setRouteStatus(payload.message || 'No GTFS ZIP has been uploaded yet.');
          return;
        }

        if (overlayVisible) {
          const selected = routeSelect?.value || 'all';
          setRouteStatus(
            `${payload.routeCount} route${payload.routeCount === 1 ? '' : 's'} loaded. ${selected === 'all' ? 'Showing all uploaded routes.' : `Showing route ${selected}.`}`,
          );
        } else {
          setRouteStatus(`${payload.routeCount} route${payload.routeCount === 1 ? '' : 's'} loaded. Enable overlay to display paths.`);
        }
      } catch (error) {
        applyRouteOverlay(emptyRouteFeatureCollection, false);
        setRouteControlsEnabled(false);
        setRouteStatus(error.message || 'Unable to load static route data.');
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
      loadStaticRoutes();
    } else {
      map.once('load', () => {
        startVehicleRefresh();
        loadStaticRoutes();
      });
      window.setTimeout(startVehicleRefresh, 1500);
    }

    applyZoomStyling();
    map.on('zoom', applyZoomStyling);

    setRouteControlsEnabled(false);
    setRouteStatus('Load a GTFS ZIP from Users to display static route paths.');

    if (routeToggle) {
      routeToggle.addEventListener('change', () => {
        if (!routeToggle.checked) {
          setRouteControlsEnabled(false);
          applyRouteOverlay(emptyRouteFeatureCollection, false);
          loadStaticRoutes();
          return;
        }
        loadStaticRoutes();
      });
    }

    if (routeSelect) {
      routeSelect.addEventListener('change', () => {
        loadStaticRoutes();
      });
    }

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
  const app = document.querySelector('#driving-hours-app');
  const segmentForm = document.querySelector('#segment-form');
  const segmentList = document.querySelector('#segment-list');
  const clearButton = document.querySelector('#clear-segments');
  const cancelEditButton = document.querySelector('#cancel-segment-edit');
  const formMessage = document.querySelector('#segment-message');
  const metricsPanel = document.querySelector('#hours-metrics');
  const alertsPanel = document.querySelector('#hours-alerts');
  const saveSnapshotButton = document.querySelector('#save-snapshot');
  const savedSnapshotsPanel = document.querySelector('#saved-snapshots');
  const savedSummary = document.querySelector('#saved-summary');
  const snapshotSearchInput = document.querySelector('#snapshot-search');
  const activeUserLabel = document.querySelector('#active-user-label');
  const driverNameInput = document.querySelector('#driver-name');
  const employeeNumberInput = document.querySelector('#employee-number');

  if (
    !app || !segmentForm || !segmentList || !clearButton || !cancelEditButton || !metricsPanel || !alertsPanel || !saveSnapshotButton
    || !savedSnapshotsPanel || !savedSummary || !snapshotSearchInput || !driverNameInput || !employeeNumberInput
  ) {
    return;
  }

  document.body.classList.add('driving-hours-active');

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
  let snapshots = [];
  let editingSegmentId = null;

  const normalizeForSearch = (value) => String(value || '').toLowerCase().trim();

  const escapeHtml = (value) => {
    return String(value)
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#39;');
  };

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
        status: 'compliant',
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
      status: breaches.length ? 'breached' : 'compliant',
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
      .map((message) => `<div class="hours-alert breach">${escapeHtml(message)}</div>`)
      .join('');
  };

  const getFilteredSnapshots = () => {
    const query = normalizeForSearch(snapshotSearchInput.value);
    if (!query) {
      return snapshots;
    }

    return snapshots.filter((snapshot) => {
      const haystack = [
        snapshot.driverName,
        snapshot.employeeNumber,
      ]
        .map((item) => normalizeForSearch(item))
        .join(' ');
      return haystack.includes(query);
    });
  };

  const renderSavedSnapshots = () => {
    if (!snapshots.length) {
      savedSummary.textContent = 'No snapshots saved yet.';
      savedSnapshotsPanel.innerHTML = '<p class="hours-empty">No snapshots saved yet.</p>';
      return;
    }

    const filteredSnapshots = getFilteredSnapshots();
    const totalCount = snapshots.length;
    const filteredCount = filteredSnapshots.length;
    const hasQuery = normalizeForSearch(snapshotSearchInput.value).length > 0;

    savedSummary.textContent = hasQuery
      ? `${filteredCount} of ${totalCount} snapshot${totalCount === 1 ? '' : 's'} shown.`
      : `${totalCount} snapshot${totalCount === 1 ? '' : 's'} stored.`;

    if (!filteredSnapshots.length) {
      savedSnapshotsPanel.innerHTML = '<p class="hours-empty">No snapshots match your search.</p>';
      return;
    }

    savedSnapshotsPanel.innerHTML = filteredSnapshots
      .map((snapshot) => {
        const timestamp = snapshot.createdAtEpoch
          ? new Date(snapshot.createdAtEpoch * 1000)
          : new Date(snapshot.createdAt);
        const statusClass = snapshot.status === 'breached' ? 'breached' : 'compliant';
        const statusLabel = snapshot.status === 'breached' ? 'Breached' : 'Compliant';
        return `
          <article class="saved-item ${statusClass}">
            <header class="saved-head">
              <strong>${escapeHtml(snapshot.driverName)}</strong>
              <span>${escapeHtml(snapshot.employeeNumber)}</span>
              <time>${timestamp.toLocaleString()}</time>
            </header>
            <p class="saved-summary-line">${escapeHtml(snapshot.segmentSummary)}</p>
            <p class="saved-status ${statusClass}">${statusLabel}</p>
          </article>
        `;
      })
      .join('');
  };

  const resetSegmentFormMode = () => {
    editingSegmentId = null;
    const submitButton = segmentForm.querySelector('button[type="submit"]');
    if (submitButton) {
      submitButton.textContent = 'Add';
    }
    cancelEditButton.hidden = true;
  };

  const setSegmentFormForEdit = (segment) => {
    editingSegmentId = segment.id;
    segmentForm.type.value = segment.type;
    segmentForm.start.value = formatMinutesAsTime(segment.startMinutes);
    segmentForm.end.value = formatMinutesAsTime(segment.endMinutes);
    const submitButton = segmentForm.querySelector('button[type="submit"]');
    if (submitButton) {
      submitButton.textContent = 'Update';
    }
    cancelEditButton.hidden = false;
    setMessage(formMessage, 'Editing selected segment. Update times/type and click Update.', 'success');
  };

  const renderSegments = () => {
    sortSegments();

    if (segments.length === 0) {
      segmentList.innerHTML = '<tr><td colspan="5" class="hours-empty">No segments logged yet.</td></tr>';
      const emptySummary = calculateCompliance([]);
      renderMetrics(emptySummary);
      renderAlerts(emptySummary);
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
            <td>
              <div class="segment-actions">
                <button class="btn secondary compact" type="button" data-edit-segment="${segment.id}">Edit</button>
                <button class="btn secondary compact" type="button" data-remove-segment="${segment.id}">Remove</button>
              </div>
            </td>
          </tr>
        `;
      })
      .join('');

    const summary = calculateCompliance(segments);
    renderMetrics(summary);
    renderAlerts(summary);
  };

  const loadSnapshots = async () => {
    if (!window.OCC_ASSIST.drivingSnapshotsUrl) {
      return;
    }

    const response = await fetch(window.OCC_ASSIST.drivingSnapshotsUrl, { cache: 'no-store' });
    const payload = await response.json();
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message || 'Unable to load saved snapshots.');
    }

    snapshots = payload.snapshots || [];
    renderSavedSnapshots();
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

    if (hasOverlap(startMinutes, endMinutes) && !editingSegmentId) {
      setMessage(formMessage, 'This segment overlaps an existing segment. Remove or adjust the overlap first.', 'error');
      return;
    }

    if (editingSegmentId) {
      const existing = segments.find((segment) => segment.id === editingSegmentId);
      if (!existing) {
        resetSegmentFormMode();
        setMessage(formMessage, 'Segment no longer exists. Please add it again.', 'error');
        segmentForm.reset();
        return;
      }

      const overlapsOther = segments.some((segment) => {
        if (segment.id === editingSegmentId) {
          return false;
        }
        return !(endMinutes <= segment.startMinutes || startMinutes >= segment.endMinutes);
      });

      if (overlapsOther) {
        setMessage(formMessage, 'Updated segment overlaps another segment. Adjust the times and try again.', 'error');
        return;
      }

      existing.type = type;
      existing.startMinutes = startMinutes;
      existing.endMinutes = endMinutes;
      renderSegments();
      setMessage(formMessage, 'Segment updated.', 'success');
      segmentForm.reset();
      resetSegmentFormMode();
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
    resetSegmentFormMode();
  });

  segmentList.addEventListener('click', (event) => {
    const trigger = event.target;
    if (!(trigger instanceof HTMLElement)) {
      return;
    }

    const removeId = trigger.getAttribute('data-remove-segment');
    const editId = trigger.getAttribute('data-edit-segment');

    if (editId) {
      const segment = segments.find((item) => item.id === editId);
      if (!segment) {
        setMessage(formMessage, 'Unable to edit that segment right now.', 'error');
        return;
      }
      setSegmentFormForEdit(segment);
      return;
    }

    if (!removeId) {
      return;
    }

    segments = segments.filter((segment) => segment.id !== removeId);
    if (editingSegmentId === removeId) {
      resetSegmentFormMode();
      segmentForm.reset();
    }
    renderSegments();
    setMessage(formMessage, 'Segment removed.', 'success');
  });

  clearButton.addEventListener('click', () => {
    segments = [];
    resetSegmentFormMode();
    segmentForm.reset();
    renderSegments();
    setMessage(formMessage, 'All segments cleared.', 'success');
  });

  cancelEditButton.addEventListener('click', () => {
    resetSegmentFormMode();
    segmentForm.reset();
    setMessage(formMessage, 'Edit cancelled.', 'success');
  });

  snapshotSearchInput.addEventListener('input', () => {
    renderSavedSnapshots();
  });

  saveSnapshotButton.addEventListener('click', async () => {
    const driverName = driverNameInput.value.trim();
    const employeeNumber = employeeNumberInput.value.trim();

    if (!driverName || !employeeNumber) {
      setMessage(formMessage, 'Enter the driver name and employee number before saving.', 'error');
      return;
    }

    if (!segments.length) {
      setMessage(formMessage, 'Add at least one segment before saving.', 'error');
      return;
    }

    const confirmed = window.confirm('Confirm the entries are correct and save this snapshot?');
    if (!confirmed) {
      return;
    }

    const orderedSegments = [...segments]
      .sort((left, right) => left.startMinutes - right.startMinutes)
      .map((segment) => ({
        type: segment.type,
        start: formatMinutesAsTime(segment.startMinutes),
        end: formatMinutesAsTime(segment.endMinutes),
      }));

    const response = await fetch(window.OCC_ASSIST.drivingSnapshotsUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        driverName,
        employeeNumber,
        segments: orderedSegments,
      }),
    });
    const payload = await response.json();

    if (!response.ok || !payload.ok) {
      setMessage(formMessage, payload.message || 'Unable to save snapshot.', 'error');
      return;
    }

    snapshots.unshift(payload.snapshot);
    renderSavedSnapshots();
    setMessage(formMessage, 'Snapshot saved to your account.', 'success');
  });

  const currentUserEmail = window.OCC_ASSIST.currentUser?.email;
  if (currentUserEmail && activeUserLabel) {
    activeUserLabel.textContent = `Signed in as ${currentUserEmail}`;
  }

  renderSegments();
  resetSegmentFormMode();
  loadSnapshots().catch((error) => {
    setMessage(formMessage, error.message || 'Unable to load saved snapshots.', 'error');
    renderSavedSnapshots();
  });
}

function initializeDailyOverview() {
  const overviewRoot = document.querySelector('#daily-overview');
  const refreshButton = document.querySelector('#refresh-overview');
  const overviewMessage = document.querySelector('#overview-message');
  const toggleUpcomingButton = document.querySelector('#toggle-upcoming');
  const upcomingControls = document.querySelector('#upcoming-controls');
  const upcomingScope = document.querySelector('#upcoming-scope');
  const upcomingIncludeRest = document.querySelector('#upcoming-include-rest');
  const upcomingPrev = document.querySelector('#upcoming-prev');
  const upcomingNext = document.querySelector('#upcoming-next');
  const upcomingRefresh = document.querySelector('#upcoming-refresh');
  const upcomingPeriod = document.querySelector('#upcoming-period');
  const upcomingMessage = document.querySelector('#upcoming-message');
  const upcomingList = document.querySelector('#upcoming-list');
  const currentStatus = document.querySelector('#current-shift-status');
  const currentWindow = document.querySelector('#current-shift-window');
  const currentLocation = document.querySelector('#current-shift-location');
  const nextStatus = document.querySelector('#next-shift-status');
  const nextWindow = document.querySelector('#next-shift-window');
  const nextLocation = document.querySelector('#next-shift-location');

  if (
    !overviewRoot || !refreshButton || !overviewMessage || !currentStatus || !currentWindow || !currentLocation
    || !nextStatus || !nextWindow || !nextLocation || !toggleUpcomingButton || !upcomingControls
    || !upcomingScope || !upcomingIncludeRest || !upcomingPrev || !upcomingNext || !upcomingRefresh || !upcomingPeriod
    || !upcomingMessage || !upcomingList
  ) {
    return;
  }

  let upcomingVisible = false;
  let upcomingOffset = 0;

  const escapeHtml = (value) => String(value || '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');

  const renderShiftCard = (target, shift, emptyLabel) => {
    target.status.textContent = shift ? shift.summary : emptyLabel;
    target.window.textContent = shift ? shift.windowLabel : '';
    target.location.textContent = shift && shift.location ? shift.location : '';
  };

  const loadOverview = async () => {
    setMessage(overviewMessage, 'Loading rota shifts...');
    const response = await fetch(window.OCC_ASSIST.overviewShiftsUrl, { cache: 'no-store' });
    const payload = await response.json();

    if (!response.ok || !payload.ok) {
      setMessage(overviewMessage, payload.message || 'Unable to load rota shifts right now.', 'error');
      renderShiftCard(
        { status: currentStatus, window: currentWindow, location: currentLocation },
        null,
        'Unavailable',
      );
      renderShiftCard(
        { status: nextStatus, window: nextWindow, location: nextLocation },
        null,
        'Unavailable',
      );
      return;
    }

    if (!payload.configured) {
      setMessage(overviewMessage, 'No RotaCloud iCal configured. Use the settings cog to add your link.', 'error');
      renderShiftCard(
        { status: currentStatus, window: currentWindow, location: currentLocation },
        null,
        'No active shift',
      );
      renderShiftCard(
        { status: nextStatus, window: nextWindow, location: nextLocation },
        null,
        'No upcoming shift',
      );
      return;
    }

    renderShiftCard(
      { status: currentStatus, window: currentWindow, location: currentLocation },
      payload.currentShift,
      'No active shift',
    );
    renderShiftCard(
      { status: nextStatus, window: nextWindow, location: nextLocation },
      payload.nextShift,
      'No upcoming shift',
    );
    setMessage(overviewMessage, 'Shift data loaded.', 'success');
  };

  const renderUpcomingShifts = (items) => {
    if (!items.length) {
      upcomingList.innerHTML = '<p class="hours-empty">No upcoming shifts in this period.</p>';
      return;
    }

    upcomingList.innerHTML = items
      .map((shift) => `
        <article class="overview-card upcoming-item">
          <h3>${escapeHtml(shift.summary)}</h3>
          <p class="overview-window">${escapeHtml(shift.windowLabel)}</p>
          <p class="overview-location">${escapeHtml(shift.location || '')}</p>
        </article>
      `)
      .join('');
  };

  const loadUpcomingShifts = async () => {
    if (!upcomingVisible) {
      return;
    }

    setMessage(upcomingMessage, 'Loading upcoming shifts...');
    const query = new URLSearchParams({
      scope: upcomingScope.value,
      offset: String(upcomingOffset),
      includeRestDays: upcomingIncludeRest.checked ? '1' : '0',
    });

    const response = await fetch(`${window.OCC_ASSIST.overviewUpcomingUrl}?${query.toString()}`, {
      cache: 'no-store',
    });
    const payload = await response.json();

    if (!response.ok || !payload.ok) {
      setMessage(upcomingMessage, payload.message || 'Unable to load upcoming shifts.', 'error');
      upcomingPeriod.textContent = '';
      upcomingList.innerHTML = '';
      return;
    }

    if (!payload.configured) {
      setMessage(upcomingMessage, 'No RotaCloud iCal configured. Use settings to add your link.', 'error');
      upcomingPeriod.textContent = '';
      upcomingList.innerHTML = '';
      return;
    }

    upcomingPeriod.textContent = `${payload.scope === 'week' ? 'Week' : 'Month'}: ${payload.periodLabel}`;
    setMessage(upcomingMessage, `Loaded ${payload.shifts.length} shift${payload.shifts.length === 1 ? '' : 's'}.`, 'success');
    renderUpcomingShifts(payload.shifts || []);
  };

  const setUpcomingVisibility = (visible) => {
    upcomingVisible = visible;
    upcomingControls.hidden = !visible;
    upcomingPeriod.hidden = !visible;
    upcomingMessage.hidden = !visible;
    upcomingList.hidden = !visible;
    toggleUpcomingButton.textContent = visible ? 'Hide Upcoming Shifts' : 'Show Upcoming Shifts';
    if (visible) {
      loadUpcomingShifts();
    }
  };

  refreshButton.addEventListener('click', () => {
    loadOverview();
  });

  toggleUpcomingButton.addEventListener('click', () => {
    setUpcomingVisibility(!upcomingVisible);
  });

  upcomingScope.addEventListener('change', () => {
    upcomingOffset = 0;
    loadUpcomingShifts();
  });

  upcomingIncludeRest.addEventListener('change', () => {
    loadUpcomingShifts();
  });

  upcomingPrev.addEventListener('click', () => {
    upcomingOffset -= 1;
    loadUpcomingShifts();
  });

  upcomingNext.addEventListener('click', () => {
    upcomingOffset += 1;
    loadUpcomingShifts();
  });

  upcomingRefresh.addEventListener('click', () => {
    loadUpcomingShifts();
  });

  setUpcomingVisibility(false);
  loadOverview();
}

function initializeSettingsPage() {
  const settingsForm = document.querySelector('#settings-form');
  const settingsMessage = document.querySelector('#settings-message');
  const icalInput = document.querySelector('#rotacloud-ical-url');

  if (!settingsForm || !settingsMessage || !icalInput) {
    return;
  }

  const loadSettings = async () => {
    setMessage(settingsMessage, 'Loading settings...');
    const response = await fetch(window.OCC_ASSIST.settingsRotacloudUrl, { cache: 'no-store' });
    const payload = await response.json();
    if (!response.ok || !payload.ok) {
      setMessage(settingsMessage, payload.message || 'Unable to load settings.', 'error');
      return;
    }

    icalInput.value = payload.rotacloudIcalUrl || '';
    setMessage(settingsMessage, '');
  };

  settingsForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    setMessage(settingsMessage, 'Saving settings...');

    const response = await fetch(window.OCC_ASSIST.updateSettingsRotacloudUrl, {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        rotacloudIcalUrl: icalInput.value.trim(),
      }),
    });

    const payload = await response.json();
    if (!response.ok || !payload.ok) {
      setMessage(settingsMessage, payload.message || 'Unable to save settings.', 'error');
      return;
    }

    setMessage(settingsMessage, 'Settings saved.', 'success');
  });

  loadSettings();
}

function initializeUsersPage() {
  const userForm = document.querySelector('#user-form');
  const usersList = document.querySelector('#users-list');
  const refreshButton = document.querySelector('#refresh-users');
  const usersMessage = document.querySelector('#users-message');
  const formMessage = document.querySelector('#user-form-message');
  const gtfsUploadForm = document.querySelector('#gtfs-upload-form');
  const gtfsFileInput = document.querySelector('#gtfs-file');
  const gtfsUploadMessage = document.querySelector('#gtfs-upload-message');
  const gtfsUploadSummary = document.querySelector('#gtfs-upload-summary');
  const refreshGtfsStatusButton = document.querySelector('#refresh-gtfs-status');

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

  const loadGtfsStatus = async () => {
    if (!window.OCC_ASSIST.gtfsStatusUrl || !gtfsUploadSummary) {
      return;
    }

    const response = await fetch(window.OCC_ASSIST.gtfsStatusUrl, { cache: 'no-store' });
    const payload = await response.json();
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message || 'Unable to load GTFS status.');
    }

    if (!payload.configured) {
      gtfsUploadSummary.textContent = payload.message || 'No GTFS ZIP uploaded yet.';
      return;
    }

    const uploadedAt = payload.uploadedAt ? new Date(payload.uploadedAt).toLocaleString() : 'Unknown';
    const filename = payload.originalFilename || 'Uploaded file';
    gtfsUploadSummary.textContent = `${payload.routeCount} routes available from ${filename} (uploaded ${uploadedAt}).`;
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

  if (gtfsUploadForm && gtfsFileInput && gtfsUploadMessage && gtfsUploadSummary) {
    gtfsUploadForm.addEventListener('submit', async (event) => {
      event.preventDefault();

      const file = gtfsFileInput.files && gtfsFileInput.files[0] ? gtfsFileInput.files[0] : null;
      if (!file) {
        setMessage(gtfsUploadMessage, 'Choose a GTFS ZIP file before uploading.', 'error');
        return;
      }

      const formData = new FormData();
      formData.append('gtfsZipFile', file);
      setMessage(gtfsUploadMessage, 'Uploading GTFS ZIP and extracting route paths...');

      const response = await fetch(window.OCC_ASSIST.gtfsUploadUrl, {
        method: 'POST',
        body: formData,
      });
      const payload = await response.json();

      if (!response.ok || !payload.ok) {
        setMessage(gtfsUploadMessage, payload.message || 'Unable to upload GTFS ZIP file.', 'error');
        return;
      }

      setMessage(gtfsUploadMessage, `Upload complete. ${payload.routeCount} routes extracted.`, 'success');
      gtfsUploadForm.reset();
      await loadGtfsStatus();
    });

    if (refreshGtfsStatusButton) {
      refreshGtfsStatusButton.addEventListener('click', () => {
        loadGtfsStatus().catch((error) => {
          setMessage(gtfsUploadMessage, error.message || 'Unable to refresh GTFS status.', 'error');
        });
      });
    }
  }

  loadUsers();
  if (gtfsUploadSummary) {
    loadGtfsStatus().catch((error) => {
      if (gtfsUploadMessage) {
        setMessage(gtfsUploadMessage, error.message || 'Unable to load GTFS status.', 'error');
      }
    });
  }
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