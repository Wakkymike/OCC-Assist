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
  initializeServiceOverview();
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

function escapeHtml(value) {
  return String(value || '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function normalizeTrackingKey(value) {
  return String(value || '').toLowerCase().replace(/[^a-z0-9]+/g, '');
}

function formatVehicleDirection(value) {
  const direction = String(value || '').trim().toLowerCase();
  if (direction === 'inbound') {
    return 'Inbound';
  }
  if (direction === 'outbound') {
    return 'Outbound';
  }
  return direction ? direction.charAt(0).toUpperCase() + direction.slice(1) : 'Unknown';
}

function formatBoardNumber(vehicle) {
  return String(
    vehicle?.boardNumber
      || vehicle?.blockRef
      || vehicle?.journeyCode
      || vehicle?.vehicleJourneyRef
      || vehicle?.journeyRef
      || 'Unknown',
  ).trim() || 'Unknown';
}

function formatJourneyNumber(vehicle) {
  return String(
    vehicle?.journeyCode
      || vehicle?.vehicleJourneyRef
      || vehicle?.journeyRef
      || 'Unknown',
  ).trim() || 'Unknown';
}

function formatJourneyOriginDeparture(vehicle) {
  const rawTime = (
    vehicle?.originAimedDepartureTime
      || vehicle?.originDepartureTime
      || vehicle?.firstStopDepartureTime
      || ''
  );
  const value = String(rawTime).trim();
  if (!value) {
    return 'Not available';
  }

  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }

  return parsed.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function formatLastStop(lastStop) {
  if (!lastStop) {
    return 'Not yet available';
  }
  return String(lastStop.name || 'Unknown stop').trim() || 'Unknown stop';
}

function formatRouteLabel(vehicle) {
  return String(vehicle?.routeLabel || vehicle?.service || 'Unknown').trim() || 'Unknown';
}



function initializeMap() {
  const mapContainer = document.querySelector('#map');
  const mapStatus = document.querySelector('#map-status');
  const routeToggle = document.querySelector('#static-routes-toggle');
  const routeSelect = document.querySelector('#static-route-select');
  const directionSelect = document.querySelector('#static-direction-select');
  const routeStatus = document.querySelector('#map-route-status');
  const stopToggle = document.querySelector('#bus-stops-toggle');
  const trackingApp = document.querySelector('#tracking-app');
  const sidebarEmpty = document.querySelector('#tracking-sidebar-empty');
  const sidebarPanel = document.querySelector('#tracking-sidebar-panel');
  const selectedService = document.querySelector('#tracking-selected-service');
  const selectedRoute = document.querySelector('#tracking-selected-route');
  const selectedFleet = document.querySelector('#tracking-selected-fleet');
  const selectedDirection = document.querySelector('#tracking-selected-direction');
  const selectedDirectionLabel = document.querySelector('#tracking-selected-direction-label');
  const selectedDestination = document.querySelector('#tracking-selected-destination');
  const selectedBoard = document.querySelector('#tracking-selected-board');
  const selectedJourney = document.querySelector('#tracking-selected-journey');
  const selectedPunctuality = document.querySelector('#tracking-selected-punctuality');
  const selectedOriginDeparture = document.querySelector('#tracking-selected-origin-departure');
  const selectedLastStop = document.querySelector('#tracking-selected-last-stop');
  const selectedUpdated = document.querySelector('#tracking-selected-updated');
  const boltonCenter = [-2.428219, 53.576864];
  if (!mapContainer || typeof mapboxgl === 'undefined') {
    return;
  }

  if (trackingApp) {
    document.body.classList.add('tracking-active');
  }

  if (window.MAPBOX_TOKEN && window.MAPBOX_TOKEN !== 'YOUR_MAPBOX_ACCESS_TOKEN_HERE') {
    mapboxgl.accessToken = window.MAPBOX_TOKEN;
    const mapContainer = document.getElementById('map');
    const mapShell = document.querySelector('.map-shell-tracking');
    const syncMapContainerSize = () => {
      if (!mapContainer || !mapShell) {
        return;
      }
      const shellHeight = mapShell.clientHeight > 0 ? mapShell.clientHeight : 320;
      mapContainer.style.position = 'relative';
      mapContainer.style.width = '100%';
      mapContainer.style.height = `${shellHeight}px`;
      mapContainer.style.minHeight = `${shellHeight}px`;
    };
    syncMapContainerSize();

    const map = new mapboxgl.Map({
      container: 'map',
      style: 'mapbox://styles/mapbox/streets-v12',
      center: boltonCenter,
      zoom: 10.6,
    });

    const vehicleStates = new Map();
    const vehicleDataById = new Map();
    let selectedVehicleId = null;
    let selectedVehicleFleet = null;
    const resizeMap = () => {
      syncMapContainerSize();
      map.resize();
    };
    let refreshIntervalId = null;
    let stopFeatureCollection = { type: 'FeatureCollection', features: [] };
    let stopFeaturesLoaded = false;
    const routeSourceId = 'gnw-route-overlay-source';
    const routeOutlineLayerId = 'gnw-route-overlay-outline';
    const routeLayerId = 'gnw-route-overlay';
    const stopSourceId = 'gnw-stop-overlay-source';
    const stopLayerId = 'gnw-stop-overlay';
    const emptyRouteFeatureCollection = { type: 'FeatureCollection', features: [] };
    const emptyStopFeatureCollection = { type: 'FeatureCollection', features: [] };

    const normalizeFleetKey = (fleetNumber) => String(fleetNumber || '').trim().toLowerCase();

    const flashingJourneyNumbers = new Set([
      '8001', '8002', '8301', '8302', '8601', '8602',
      '1001', '1002', '1301', '1302', '1601', '1602',
    ]);

    const isFlashingJourney = (vehicle) => {
      const journey = formatJourneyNumber(vehicle);
      return flashingJourneyNumbers.has(String(journey || '').trim());
    };

    const directionBadgeMarkup = (direction) => {
      const normalized = String(direction || '').trim().toLowerCase();
      if (normalized === 'inbound') {
        return '<span class="vehicle-direction-badge inbound">[I]</span>';
      }
      if (normalized === 'outbound') {
        return '<span class="vehicle-direction-badge outbound">[O]</span>';
      }
      return '<span class="vehicle-direction-badge unknown">[?]</span>';
    };

    const buildVehicleFlagMarkup = (vehicle) => {
      const service = escapeHtml(formatRouteLabel(vehicle));
      const destination = escapeHtml(String(vehicle?.destination || 'Unknown destination').trim() || 'Unknown destination');
      const board = escapeHtml(formatBoardNumber(vehicle));
      return `
        <span class="vehicle-flag-line service-line">${service} ${directionBadgeMarkup(vehicle?.direction)}</span>
        <span class="vehicle-flag-line destination-line">${destination}</span>
        <span class="vehicle-flag-line board-line">RB ${board}</span>
      `;
    };

    const setRouteStatus = (message) => {
      if (routeStatus) {
        routeStatus.textContent = message;
      }
    };

    const removeVehicleMarker = (state) => {
      if (!state.marker) {
        return;
      }
      state.marker.remove();
      state.marker = null;
      state.flag = null;
      state.element = null;
    };

    const syncSelectedMarkerStyles = () => {
      vehicleStates.forEach((state, vehicleId) => {
        if (state.element) {
          state.element.classList.toggle('is-selected', vehicleId === selectedVehicleId);
        }
      });
    };

    const setSidebarEmpty = (message) => {
      if (sidebarEmpty) {
        sidebarEmpty.textContent = message;
        sidebarEmpty.hidden = false;
      }
      if (sidebarPanel) {
        sidebarPanel.hidden = true;
      }
    };

    const setSidebarVehicle = (vehicle) => {
      if (!vehicle) {
        setSidebarEmpty('Select a bus marker to inspect its service details.');
        return;
      }

      if (sidebarEmpty) {
        sidebarEmpty.hidden = true;
      }
      if (sidebarPanel) {
        sidebarPanel.hidden = false;
      }
      const fleetDisplay = String(vehicle.fleetNumber || 'Unknown').trim() || 'Unknown';
      if (selectedService) selectedService.textContent = fleetDisplay;
      if (selectedRoute) selectedRoute.textContent = formatRouteLabel(vehicle);
      if (selectedFleet) selectedFleet.textContent = fleetDisplay;
      if (selectedDirection) selectedDirection.textContent = formatVehicleDirection(vehicle.direction);
      if (selectedDirectionLabel) selectedDirectionLabel.textContent = formatVehicleDirection(vehicle.direction);
      if (selectedDestination) selectedDestination.textContent = String(vehicle.destination || 'Unknown').trim() || 'Unknown';
      if (selectedBoard) selectedBoard.textContent = formatBoardNumber(vehicle);
      const journeyNumber = formatJourneyNumber(vehicle);
      if (selectedJourney) {
        selectedJourney.textContent = journeyNumber;
        selectedJourney.classList.toggle('journey-flash', isFlashingJourney(vehicle));
      }
      if (selectedPunctuality) {
        const punctuality = vehicle?.punctuality || {};
        const punctualityLabel = punctuality.label || 'Unknown';
        selectedPunctuality.textContent = punctualityLabel;
        selectedPunctuality.className = `sidebar-pill punctuality-pill ${punctuality.tone || 'neutral'}`;
      }
      if (selectedOriginDeparture) selectedOriginDeparture.textContent = formatJourneyOriginDeparture(vehicle);
      if (selectedLastStop) selectedLastStop.textContent = formatLastStop(vehicle.lastStopPassed);
      if (selectedUpdated) selectedUpdated.textContent = `Updated ${formatFeedTime(vehicle.recordedAt || vehicle.sourceTimestamp || vehicle.refreshedAt)}`;
    };

    const selectVehicle = (vehicleId) => {
      selectedVehicleId = vehicleId;
      if (!vehicleId) {
        selectedVehicleFleet = null;
        setSidebarEmpty('Select a bus marker to inspect its service details.');
        syncSelectedMarkerStyles();
        return;
      }
      const selectedVehicle = vehicleDataById.get(vehicleId) || null;
      selectedVehicleFleet = normalizeFleetKey(selectedVehicle?.fleetNumber);
      setSidebarVehicle(selectedVehicle);
      syncSelectedMarkerStyles();
    };

    const ensureVehicleMarker = (state, lngLat, vehicle) => {
      const direction = String(vehicle.direction || 'unknown').trim().toLowerCase();
      const fleetDisplay = String(vehicle.fleetNumber || 'Unknown').trim() || 'Unknown';
      if (state.marker) {
        state.marker.setLngLat(lngLat);
        state.flag.dataset.direction = direction;
        state.flag.innerHTML = buildVehicleFlagMarkup(vehicle);
        state.pin.textContent = fleetDisplay;
        state.element.classList.toggle('is-flashing-journey', isFlashingJourney(vehicle));
        state.element.classList.toggle('is-selected', vehicle.id === selectedVehicleId);
        state.data = vehicle;
        return;
      }

      const markerElement = document.createElement('div');
      markerElement.className = 'vehicle-marker';
      markerElement.classList.toggle('is-flashing-journey', isFlashingJourney(vehicle));

      const flag = document.createElement('div');
      flag.className = 'vehicle-flag';
      flag.dataset.direction = direction;
      flag.innerHTML = buildVehicleFlagMarkup(vehicle);

      const pin = document.createElement('div');
      pin.className = 'vehicle-pin';
      pin.textContent = fleetDisplay;

      markerElement.append(flag, pin);
      markerElement.addEventListener('click', (event) => {
        event.stopPropagation();
        selectVehicle(vehicle.id);
      });

      state.flag = flag;
      state.pin = pin;
      state.element = markerElement;
      state.data = vehicle;
      state.marker = new mapboxgl.Marker({ element: markerElement, anchor: 'bottom' }).setLngLat(lngLat).addTo(map);
    };

    const applyZoomStyling = () => {
      const zoom = map.getZoom();
      const normalized = Math.max(0, Math.min(1, (zoom - 9.5) / 4.5));
      const flagScale = 0.3 + normalized * 0.7;
      const flagOpacity = 0.18 + normalized * 0.82;
      vehicleStates.forEach((state) => {
        if (state.element) {
          state.element.style.setProperty('--vehicle-flag-scale', flagScale.toFixed(2));
          state.element.style.setProperty('--vehicle-flag-opacity', flagOpacity.toFixed(2));
        }
      });
    };

    const setRouteControlsEnabled = (enabled) => {
      if (routeSelect) routeSelect.disabled = !enabled;
      if (directionSelect) directionSelect.disabled = !enabled;
    };

    const ensureRouteOverlayLayers = () => {
      if (map.getSource(routeSourceId)) return;
      map.addSource(routeSourceId, { type: 'geojson', data: emptyRouteFeatureCollection });
      map.addLayer({ id: routeOutlineLayerId, type: 'line', source: routeSourceId, paint: { 'line-color': '#07121b', 'line-width': 8, 'line-opacity': 0.88 } });
      map.addLayer({ id: routeLayerId, type: 'line', source: routeSourceId, paint: { 'line-color': ['match', ['get', 'direction'], 'inbound', '#23c36b', 'outbound', '#d43f3a', '#35deff'], 'line-width': 4, 'line-opacity': 0.95 } });
    };

    const ensureStopOverlayLayers = () => {
      if (map.getSource(stopSourceId)) return;
      map.addSource(stopSourceId, { type: 'geojson', data: emptyStopFeatureCollection });
      map.addLayer({ id: stopLayerId, type: 'circle', source: stopSourceId, paint: { 'circle-color': '#5fc1ff', 'circle-radius': ['interpolate', ['linear'], ['zoom'], 8, 2.4, 12, 3.8, 15, 5.2], 'circle-stroke-color': '#ffffff', 'circle-stroke-width': 1, 'circle-opacity': 0.55 } });
    };

    const applyRouteOverlay = (featureCollection, showOverlay) => {
      if (!map.isStyleLoaded()) return;
      ensureRouteOverlayLayers();
      const source = map.getSource(routeSourceId);
      if (!source) return;
      source.setData(showOverlay ? featureCollection : emptyRouteFeatureCollection);
      const visibility = showOverlay ? 'visible' : 'none';
      if (map.getLayer(routeOutlineLayerId)) map.setLayoutProperty(routeOutlineLayerId, 'visibility', visibility);
      if (map.getLayer(routeLayerId)) map.setLayoutProperty(routeLayerId, 'visibility', visibility);
    };

    const applyStopOverlay = (featureCollection, showOverlay) => {
      if (!map.isStyleLoaded()) return;
      ensureStopOverlayLayers();
      const source = map.getSource(stopSourceId);
      if (!source) return;
      source.setData(showOverlay ? featureCollection : emptyStopFeatureCollection);
      const visibility = showOverlay ? 'visible' : 'none';
      if (map.getLayer(stopLayerId)) map.setLayoutProperty(stopLayerId, 'visibility', visibility);
    };

    const updateRouteOptions = (routes, selectedRouteValue) => {
      if (!routeSelect) return;
      const currentSelection = selectedRouteValue || routeSelect.value || 'all';
      routeSelect.innerHTML = ['<option value="all">All uploaded routes</option>', ...routes.map((route) => `<option value="${escapeHtml(route.id || '')}">${escapeHtml(route.label || route.lineName || route.id || 'Route')}</option>`)].join('');
      const routeIds = routes.map((route) => String(route.id || ''));
      routeSelect.value = currentSelection === 'all' || routeIds.includes(currentSelection) ? currentSelection : 'all';
    };

    const loadTrackingStops = async () => {
      if (!window.OCC_ASSIST.trackingStopsUrl) return;
      try {
        const response = await fetch(window.OCC_ASSIST.trackingStopsUrl, { cache: 'no-store' });
        const payload = await response.json();
        if (!response.ok || !payload.ok) throw new Error(payload.message || 'Unable to load stop data.');
        const stops = payload.stops || [];
        stopFeatureCollection = { type: 'FeatureCollection', features: stops.map((stop) => ({ type: 'Feature', geometry: { type: 'Point', coordinates: [stop.longitude, stop.latitude] }, properties: { stopId: stop.id, name: stop.name } })) };
        stopFeaturesLoaded = true;
        if (stopToggle) stopToggle.disabled = false;
        applyStopOverlay(stopFeatureCollection, Boolean(stopToggle?.checked));
      } catch (error) {
        stopFeatureCollection = emptyStopFeatureCollection;
        stopFeaturesLoaded = false;
        applyStopOverlay(emptyStopFeatureCollection, false);
        if (stopToggle) stopToggle.disabled = true;
      }
    };

    const renderVehicles = (vehicles, observedAtMs) => {
      const activeIds = new Set();
      let visibleVehicleCount = 0;
      vehicles.forEach((vehicle) => {
        activeIds.add(vehicle.id);
        vehicleDataById.set(vehicle.id, vehicle);
        const lngLat = [vehicle.longitude, vehicle.latitude];
        if (vehicleStates.has(vehicle.id)) {
          const vehicleState = vehicleStates.get(vehicle.id);
          ensureVehicleMarker(vehicleState, lngLat, vehicle);
          visibleVehicleCount += 1;
          return;
        }
        const vehicleState = { marker: null, flag: null, pin: null, element: null, data: vehicle };
        ensureVehicleMarker(vehicleState, lngLat, vehicle);
        vehicleStates.set(vehicle.id, vehicleState);
        visibleVehicleCount += 1;
      });
      vehicleStates.forEach((vehicleState, vehicleId) => {
        if (!activeIds.has(vehicleId)) {
          removeVehicleMarker(vehicleState);
          vehicleStates.delete(vehicleId);
          vehicleDataById.delete(vehicleId);
        }
      });
      if (selectedVehicleId && !vehicleDataById.has(selectedVehicleId) && selectedVehicleFleet) {
        const reassigned = vehicles.find((vehicle) => normalizeFleetKey(vehicle.fleetNumber) === selectedVehicleFleet);
        if (reassigned && reassigned.id) {
          selectedVehicleId = reassigned.id;
        }
      }

      if (selectedVehicleId && !vehicleDataById.has(selectedVehicleId)) {
        selectedVehicleId = null;
        selectedVehicleFleet = null;
        setSidebarEmpty('Select a bus marker to inspect its service details.');
      } else if (selectedVehicleId) {
        const selectedVehicle = vehicleDataById.get(selectedVehicleId);
        selectedVehicleFleet = normalizeFleetKey(selectedVehicle?.fleetNumber);
        setSidebarVehicle(selectedVehicle);
      }
      syncSelectedMarkerStyles();
      return visibleVehicleCount;
    };

    const refreshVehicles = async () => {
      if (!window.OCC_ASSIST.trackingVehiclesUrl) return;
      try {
        const response = await fetch(window.OCC_ASSIST.trackingVehiclesUrl, { cache: 'no-store' });
        const payload = await response.json();
        if (!response.ok || !payload.ok) throw new Error(payload.message || 'Unable to load vehicle positions.');
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
        const selectedRouteValue = routeSelect?.value || 'all';
        const selectedDirectionValue = directionSelect?.value || 'all';
        const query = new URLSearchParams({ route: selectedRouteValue, direction: selectedDirectionValue });
        const response = await fetch(`${window.OCC_ASSIST.trackingStaticRoutesUrl}?${query.toString()}`, { cache: 'no-store' });
        const payload = await response.json();
        if (!response.ok || !payload.ok) throw new Error(payload.message || 'Unable to load static route data.');
        const routes = payload.routes || [];
        updateRouteOptions(routes, payload.selectedRoute || 'all');
        if (directionSelect) directionSelect.value = payload.selectedDirection || selectedDirectionValue;
        const overlayVisible = Boolean(routeToggle?.checked) && payload.configured;
        applyRouteOverlay(payload.featureCollection || emptyRouteFeatureCollection, overlayVisible);
        setRouteControlsEnabled(Boolean(routeToggle?.checked) && payload.configured);
        if (!payload.configured) {
          setRouteStatus(payload.message || 'No GTFS ZIP has been uploaded yet.');
          return;
        }
        if (overlayVisible) {
          const selected = routeSelect?.value || 'all';
          const directionLabel = selectedDirectionValue === 'inbound' ? 'Showing inbound trips only.' : selectedDirectionValue === 'outbound' ? 'Showing outbound trips only.' : 'Showing inbound and outbound trips.';
          setRouteStatus(`${payload.routeCount} route${payload.routeCount === 1 ? '' : 's'} loaded. ${selected === 'all' ? 'Showing all uploaded routes.' : `Showing route ${selected}.`} ${directionLabel}`);
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
      if (refreshIntervalId !== null) return;
      setMessage(mapStatus, 'Loading vehicle positions...', 'success');
      refreshVehicles();
      refreshIntervalId = window.setInterval(refreshVehicles, 7000);
    };

    const initializeOverlays = () => {
      loadTrackingStops();
      startVehicleRefresh();
      loadStaticRoutes();
      window.requestAnimationFrame(() => {
        resizeMap();
        window.requestAnimationFrame(resizeMap);
      });
      window.setTimeout(resizeMap, 250);
    };

    if (map.loaded()) {
      initializeOverlays();
    } else {
      map.once('load', initializeOverlays);
      window.setTimeout(startVehicleRefresh, 1500);
    }

    window.requestAnimationFrame(() => {
      resizeMap();
      window.requestAnimationFrame(resizeMap);
    });
    window.setTimeout(resizeMap, 250);
    window.addEventListener('resize', resizeMap);

    applyZoomStyling();
    map.on('zoom', applyZoomStyling);
    map.on('click', () => {
      selectVehicle(null);
    });

    setRouteControlsEnabled(false);
    setRouteStatus('Load a GTFS ZIP from Users to display static route paths.');
    if (sidebarEmpty) setSidebarEmpty('Select a bus marker to inspect its service details.');

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

    if (routeSelect) routeSelect.addEventListener('change', () => loadStaticRoutes());
    if (directionSelect) directionSelect.addEventListener('change', () => loadStaticRoutes());
    if (stopToggle) {
      stopToggle.addEventListener('change', () => {
        if (!stopFeaturesLoaded) {
          loadTrackingStops();
          return;
        }
        applyStopOverlay(stopFeatureCollection, stopToggle.checked);
      });
    }

    return;
  }

  mapContainer.innerHTML = '<div class="placeholder-card"><p>Mapbox token is not configured yet.</p></div>';
}


function initializeServiceOverview() {
  const app = document.querySelector('#service-overview-app');
  const refreshButton = document.querySelector('#refresh-service-overview');
  const overviewStatus = document.querySelector('#service-overview-status');
  const routeCountEl = document.querySelector('#service-overview-route-count');
  const vehicleCountEl = document.querySelector('#service-overview-vehicle-count');
  const updatedEl = document.querySelector('#service-overview-updated');
  const listEl = document.querySelector('#service-overview-list');

  if (!app || !refreshButton || !overviewStatus || !routeCountEl || !vehicleCountEl || !updatedEl || !listEl) {
    return;
  }

  const renderOverview = (vehicles, sourceTimestamp) => {
    const groups = new Map();
    vehicles.forEach((vehicle) => {
      const key = normalizeTrackingKey(vehicle.routeId || vehicle.routeLabel || vehicle.service || 'unknown');
      if (!groups.has(key)) {
        groups.set(key, {
          routeId: vehicle.routeId || vehicle.service || 'Unknown',
          routeLabel: formatRouteLabel(vehicle),
          vehicles: [],
        });
      }
      groups.get(key).vehicles.push(vehicle);
    });

    const orderedGroups = Array.from(groups.values()).sort((left, right) => left.routeLabel.localeCompare(right.routeLabel, undefined, { numeric: true, sensitivity: 'base' }));
    routeCountEl.textContent = String(orderedGroups.length);
    vehicleCountEl.textContent = String(vehicles.length);
    updatedEl.textContent = formatFeedTime(sourceTimestamp);

    if (!vehicles.length) {
      listEl.innerHTML = '<p class="saved-empty">No active services are visible right now.</p>';
      return;
    }

    listEl.innerHTML = orderedGroups.map((group) => {
      const routeVehicleCount = group.vehicles.length;
      const routeVehicles = group.vehicles.map((vehicle) => `
        <article class="service-card">
          <div class="service-card-head">
            <div>
              <p class="service-card-route">${escapeHtml(formatRouteLabel(vehicle))}</p>
              <strong>${escapeHtml(String(vehicle.destination || 'Unknown destination'))}</strong>
            </div>
            <span class="sidebar-pill">${escapeHtml(formatVehicleDirection(vehicle.direction))}</span>
          </div>
          <dl class="service-detail-list">
            <div><dt><span class="label-with-icon"><svg viewBox="0 0 24 24" class="info-icon icon-fleet" aria-hidden="true"><rect x="3.5" y="5" width="17" height="14" rx="2"/><path d="M8 9h8"/><path d="M8 12h8"/><path d="M8 15h5"/></svg><span>Fleet number</span></span></dt><dd>${escapeHtml(String(vehicle.fleetNumber || 'Unknown'))}</dd></div>
            <div><dt><span class="label-with-icon"><svg viewBox="0 0 24 24" class="info-icon icon-journey" aria-hidden="true"><path d="M7 4h10"/><path d="M6 7h12"/><rect x="5" y="4" width="14" height="16" rx="2"/><path d="M9 11h6"/><path d="M9 14h4"/></svg><span>Journey number</span></span></dt><dd>${escapeHtml(formatJourneyNumber(vehicle))}</dd></div>
            <div><dt><span class="label-with-icon"><svg viewBox="0 0 24 24" class="info-icon icon-time" aria-hidden="true"><circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 2"/></svg><span>Departed first stop</span></span></dt><dd>${escapeHtml(formatJourneyOriginDeparture(vehicle))}</dd></div>
            <div><dt><span class="label-with-icon"><svg viewBox="0 0 24 24" class="info-icon icon-board" aria-hidden="true"><rect x="6" y="5" width="12" height="16" rx="2"/><path d="M9 5.5h6v3H9z"/><path d="M9 12h6"/><path d="M9 15h4"/></svg><span>Board number</span></span></dt><dd>${escapeHtml(formatBoardNumber(vehicle))}</dd></div>
            <div><dt><span class="label-with-icon"><svg viewBox="0 0 24 24" class="info-icon icon-time" aria-hidden="true"><circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 2"/></svg><span>Early / late</span></span></dt><dd><span class="sidebar-pill punctuality-pill ${escapeHtml(vehicle?.punctuality?.tone || 'neutral')}">${escapeHtml(vehicle?.punctuality?.label || 'Unknown')}</span></dd></div>
            <div><dt><span class="label-with-icon"><svg viewBox="0 0 24 24" class="info-icon icon-stop" aria-hidden="true"><path d="M12 21s6-4.5 6-10a6 6 0 1 0-12 0c0 5.5 6 10 6 10z"/><path d="M10 9h4l-1.4 1.8L14 13h-4"/></svg><span>Last stop passed</span></span></dt><dd>${escapeHtml(formatLastStop(vehicle.lastStopPassed))}</dd></div>
          </dl>
        </article>
      `).join('');
      return `
        <section class="service-group" data-route-group="${escapeHtml(group.routeId)}">
          <header class="service-group-head">
            <div>
              <p class="brand-subtitle">Route ${escapeHtml(group.routeLabel)}</p>
              <h2>${escapeHtml(group.routeLabel)}</h2>
            </div>
            <div class="service-group-actions">
              <span class="service-count-pill">${routeVehicleCount} active</span>
              <button type="button" class="service-group-toggle" aria-expanded="true" aria-label="Collapse route ${escapeHtml(group.routeLabel)}">
                <span class="chevron">▾</span>
              </button>
            </div>
          </header>
          <div class="service-group-list">${routeVehicles}</div>
        </section>
      `;
    }).join('');

    listEl.querySelectorAll('.service-group').forEach((group) => {
      group.classList.add('is-collapsed');
      const toggle = group.querySelector('.service-group-toggle');
      if (toggle) {
        toggle.setAttribute('aria-expanded', 'false');
      }
    });
  };

  const refreshOverview = async () => {
    if (!window.OCC_ASSIST.trackingVehiclesUrl) {
      setMessage(overviewStatus, 'Vehicle API is not configured.', 'error');
      return;
    }
    try {
      const response = await fetch(window.OCC_ASSIST.trackingVehiclesUrl, { cache: 'no-store' });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.message || 'Unable to load active services.');
      renderOverview(payload.vehicles || [], payload.sourceTimestamp || payload.refreshedAt);
      setMessage(overviewStatus, `Loaded ${payload.vehicles?.length || 0} active vehicle${(payload.vehicles?.length || 0) === 1 ? '' : 's'}.`, 'success');
    } catch (error) {
      setMessage(overviewStatus, error.message || 'Unable to load active services.', 'error');
      listEl.innerHTML = '<p class="saved-empty">Unable to load active services right now.</p>';
    }
  };

  listEl.addEventListener('click', (event) => {
    const toggle = event.target.closest('.service-group-toggle');
    if (!toggle) {
      return;
    }

    const group = toggle.closest('.service-group');
    if (!group) {
      return;
    }

    const willCollapse = !group.classList.contains('is-collapsed');
    group.classList.toggle('is-collapsed', willCollapse);
    toggle.setAttribute('aria-expanded', willCollapse ? 'false' : 'true');
  });

  refreshButton.addEventListener('click', refreshOverview);
  refreshOverview();
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

  usersList.addEventListener('click', async (event) => {
    const deleteButton = event.target.closest('[data-action="delete-user"]');
    if (deleteButton) {
      event.preventDefault();
      const userId = deleteButton.dataset.userId;
      const userEmail = deleteButton.dataset.userEmail || 'this user';
      const isConfirmed = window.confirm(`Delete ${userEmail} and remove all of their saved data?`);

      if (!isConfirmed) {
        return;
      }

      setMessage(usersMessage, 'Deleting user and saved data...');
      const response = await fetch(`${window.OCC_ASSIST.permissionsBaseUrl}/${userId}`, {
        method: 'DELETE',
      });
      const payload = await response.json();

      if (!response.ok) {
        setMessage(usersMessage, payload.message || 'Unable to delete user.', 'error');
        return;
      }

      setMessage(usersMessage, 'User deleted and saved data removed.', 'success');
      await loadUsers();
      return;
    }

    const forceLogoutButton = event.target.closest('[data-action="force-logout"]');
    if (forceLogoutButton) {
      event.preventDefault();
      const userId = forceLogoutButton.dataset.userId;
      const userEmail = forceLogoutButton.dataset.userEmail || 'this user';
      const isConfirmed = window.confirm(`End the current active session for ${userEmail}?`);

      if (!isConfirmed) {
        return;
      }

      setMessage(usersMessage, 'Ending active session...');
      const response = await fetch(`${window.OCC_ASSIST.permissionsBaseUrl}/${userId}/sessions/force-logout`, {
        method: 'POST',
      });
      const payload = await response.json();

      if (!response.ok) {
        setMessage(usersMessage, payload.message || 'Unable to end the active session.', 'error');
        return;
      }

      setMessage(usersMessage, 'Active session ended.', 'success');
      await loadUsers();
      return;
    }

    const forcePasswordResetButton = event.target.closest('[data-action="force-password-reset"]');
    if (!forcePasswordResetButton) {
      return;
    }

    event.preventDefault();
    const userId = forcePasswordResetButton.dataset.userId;
    const userEmail = forcePasswordResetButton.dataset.userEmail || 'this user';
    const isConfirmed = window.confirm(`Require ${userEmail} to reset their password at next sign-in?`);

    if (!isConfirmed) {
      return;
    }

    setMessage(usersMessage, 'Requesting password reset...');
    const response = await fetch(`${window.OCC_ASSIST.permissionsBaseUrl}/${userId}/password-reset`, {
      method: 'POST',
    });
    const payload = await response.json();

    if (!response.ok) {
      setMessage(usersMessage, payload.message || 'Unable to request password reset.', 'error');
      return;
    }

    setMessage(usersMessage, 'Password reset requested.', 'success');
    await loadUsers();
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
  const currentUser = window.OCC_ASSIST.currentUser || {};
  const canDeleteUsers = Boolean(
    currentUser.isSuperadmin || currentUser.permissions?.admin_privileges || currentUser.permissions?.user_management,
  );

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

      const isSelf = Number(currentUser.id) === Number(user.id);
      const session = user.session || {};
      const sessionDurationLabel = session.sessionDurationSeconds != null
        ? `${Math.max(0, Math.floor(Number(session.sessionDurationSeconds) / 60))} min`
        : '0 min';
      const sessionStatusLabel = session.isActive ? 'Active now' : 'No active session';
      const sessionSummary = session.hasSession
        ? `${sessionStatusLabel} • Signed in for ${sessionDurationLabel}`
        : 'No active session';
      const deleteButtonMarkup = canDeleteUsers && !isSelf
        ? `<button class="btn danger compact" type="button" data-action="delete-user" data-user-id="${user.id}" data-user-email="${user.email}">Delete</button>`
        : '';
      const forceLogoutButtonMarkup = canDeleteUsers && !isSelf
        ? `<button class="btn secondary compact" type="button" data-action="force-logout" data-user-id="${user.id}" data-user-email="${user.email}">Force logout</button>`
        : '';
      const forcePasswordResetButtonMarkup = canDeleteUsers && !isSelf
        ? `<button class="btn secondary compact" type="button" data-action="force-password-reset" data-user-id="${user.id}" data-user-email="${user.email}">Force password reset</button>`
        : '';

      return `
        <article class="user-card">
          <div class="user-card-head">
            <div>
              <h3>${user.email}</h3>
              <p class="user-meta">Created ${user.createdAt}</p>
              <p class="user-meta">${sessionSummary}</p>
            </div>
            <div class="user-card-actions">
              <span class="badge">${user.isSuperadmin ? 'Superadmin' : 'Standard user'}</span>
              ${deleteButtonMarkup}
              ${forceLogoutButtonMarkup}
              ${forcePasswordResetButtonMarkup}
            </div>
          </div>
          <div class="permission-list">${permissionMarkup}</div>
        </article>
      `;
    })
    .join('');
}