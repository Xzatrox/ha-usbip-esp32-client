/**
 * USB/IP ESP32 Client - Client-side fetch polling utility.
 *
 * Provides a simple API for polling endpoints at configurable intervals
 * using fetch(). No WebSocket dependency per Req 16.2.
 */
var UsbipPolling = (function() {
    'use strict';

    var _timers = {};
    var _timerCounter = 0;

    /**
     * Start polling an endpoint at a given interval.
     *
     * @param {string} url - The API endpoint URL to poll.
     * @param {function} callback - Function called with parsed JSON data on success.
     * @param {number} intervalMs - Polling interval in milliseconds.
     * @param {object} [options] - Optional settings.
     * @param {function} [options.onError] - Error handler callback.
     * @returns {string} Timer ID that can be used with stop().
     */
    function start(url, callback, intervalMs, options) {
        options = options || {};
        var id = 'poll_' + (++_timerCounter);

        function poll() {
            fetch(url)
                .then(function(response) {
                    if (!response.ok) {
                        throw new Error('HTTP ' + response.status);
                    }
                    return response.json();
                })
                .then(function(data) {
                    callback(data);
                })
                .catch(function(error) {
                    if (options.onError) {
                        options.onError(error);
                    }
                });
        }

        // Fetch immediately, then at interval
        poll();
        _timers[id] = setInterval(poll, intervalMs);

        return id;
    }

    /**
     * Stop a polling timer.
     *
     * @param {string} id - Timer ID returned by start().
     */
    function stop(id) {
        if (_timers[id]) {
            clearInterval(_timers[id]);
            delete _timers[id];
        }
    }

    /**
     * Stop all active polling timers.
     */
    function stopAll() {
        Object.keys(_timers).forEach(function(id) {
            clearInterval(_timers[id]);
        });
        _timers = {};
    }

    return {
        start: start,
        stop: stop,
        stopAll: stopAll
    };
})();
