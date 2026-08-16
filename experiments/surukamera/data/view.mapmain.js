// ***********************************************************************
// Project          : Travelers.UI
// Author           : Graim George
// Created          : 03-03-2015
//
// Last Modified By : Graim George
// Last Modified On : 12-12-2016
// ***********************************************************************
// <summary>This module contains all the methods,events affecting the Main Map functionalities.
//          The map is built using Bing Version 7 however, Version 8 is currently available and 
//          need to upgrade.
//</summary>
// ***********************************************************************
//$(document).ready(function () {

    //mapmain.init(user.config.profile);
//});

var mapmain = {
    //configuration properties for the map and layers
    'config': {
        'mapKey': null,
        'roadSegmentLayerEnabled': false,
        'neighborhoodsLayerEnabled': false,
        'lastZoomLevel': -1,
        'lastZoomRange': 0,
        'roadSegmentLayer': null,
        'neighborhoodsLayer': null,
        'neighborhoodLabelList': null,
        'useBingTrafficCongestion': true,
        'roadSegmentLayerTimeoutId': 0,
        'cameraLayer': null,
        'cameraLayerEnabled': false,
        'alertsLayer': null,
        'alertsLayerEnabled': false,
        'alertsIntervalId': 0,
        'travelTimeLayer': null,
        'travelTimeLink': null,
        'travelTimeLayerEnabled': false,
        'dmsSignsOnLayer': null,
        'dmsSignsOnLayerEnabled': false,
        'dmsSignsOnIntervalId': 0,
        'dmsSignsOffLayer': null,
        'dmsSignsOffLayerEnabled': false,
        'dmsSignsOffIntervalId': 0,
        'bridgesOpenLayer': null,
        'bridgesOpenLayerEnabled': false,
        'bridgesOpenIntervalId': 0,
        'bridgesClosedLayer': null,
        'bridgesClosedLayerEnabled': false,
        'bridgesClosedIntervalId': 0,
        'rrCrossingsOpenLayer': null,
        'rrCrossingsOpenLayerEnabled': false,
        'rrCrossingsOpenIntervalId': 0,
        'rrCrossingsClosedLayer': null,
        'rrCrossingsClosedLayerEnabled': false,
        'rrCrossingsClosedIntervalId': 0,
        'findItShapeLayer': null,
        'currentTravelTimeInfoBox': null,
    },
    'init': function (profile) {

        //if (mapmain.config.mapKey == null)
        //{
        //    mapmain.config.mapKey = utils.getMapKey();
        //}

        //initialize layers on the map     
        mapmain.config.neighborhoodsLayerEnabled = (profile.userProfile.mapLegend.neighborhoods == true);
        mapmain.config.roadSegmentLayerEnabled = (profile.userProfile.mapLegend.congestion == true);
        mapmain.config.cameraLayerEnabled = (profile.userProfile.mapLegend.cameras == true);
        mapmain.config.alertsLayerEnabled = (profile.userProfile.mapLegend.alerts == true);
        mapmain.config.travelTimeLayerEnabled = (profile.userProfile.mapLegend.travelTimes == true);
        mapmain.config.dmsSignsOnLayerEnabled = (profile.userProfile.mapLegend.dmsSignsOn == true);
        mapmain.config.dmsSignsOffLayerEnabled = (profile.userProfile.mapLegend.dmsSignsOff == true);
        mapmain.config.bridgesOpenLayerEnabled = (profile.userProfile.mapLegend.bridgesOpen == true);
        mapmain.config.bridgesClosedLayerEnabled = (profile.userProfile.mapLegend.bridgesClosed == true);
        mapmain.config.rrCrossingsOpenLayerEnabled = (profile.userProfile.mapLegend.rrCrossingsOpen == true)
        mapmain.config.rrCrossingsClosedLayerEnabled = (profile.userProfile.mapLegend.rrCrossingsClosed == true)

        ////intialize bing map
        //mapmain.config.map = new Microsoft.Maps.Map(document.getElementById("mapDiv"),
        //                   {
        //                       credentials: mapmain.config.mapKey,
        //                       center: new Microsoft.Maps.Location(profile.userProfile.location.lat, profile.userProfile.location.lng),
        //                       mapTypeId: Microsoft.Maps.MapTypeId.road,
        //                       showDashboard: true,
        //                       showMapTypeSelector: false,
        //                       showScalebar: false,
        //                       enableSearchLogo: false,
        //                       showCopyright: true,
        //                       showLogo: false,
        //                       zoom: profile.userProfile.zoom
        //                   });

        //set map zoom range
        mapmain.config.map.setOptions({ maxZoom: MAP_ZOOM_RANGE_MAX, minZoom: MAP_ZOOM_RANGE_MIN });

        //handle panning
        Microsoft.Maps.Events.addThrottledHandler(mapmain.config.map, "viewchangeend", mapmain.onViewChangeEnd, 500);


        //attach map layers based on the configuration settings
        //if (mapmain.config.neighborhoodsLayerEnabled) { mapmain.attachNeighborhoodLayer(); }
        if (mapmain.config.roadSegmentLayerEnabled) { mapmain.attachRoadCongestionLayer(); }
        if (mapmain.config.travelTimeLayerEnabled) { mapmain.attachTravelTimeLayer(); }
        if (mapmain.config.dmsSignsOnLayerEnabled) { mapmain.attachDMSSignsOnLayer(); }
        if (mapmain.config.dmsSignsOffLayerEnabled) { mapmain.attachDMSSignsOffLayer(); }
        if (mapmain.config.bridgesOpenLayerEnabled) { mapmain.attachBridgesOpenLayer(); }
        if (mapmain.config.bridgesClosedLayerEnabled) { mapmain.attachBridgesClosedLayer(); }
        if (mapmain.config.rrCrossingsOpenLayerEnabled) { mapmain.attachRRCrossingsOpenLayer(); }
        if (mapmain.config.rrCrossingsClosedLayerEnabled) { mapmain.attachRRCrossingsClosedLayer(); }

    },
    /*Find address and locate on the map */
    'searchByAddress': function()
    {
        Microsoft.Maps.loadModule('Microsoft.Maps.Search', { callback: geocodeRequestForSearch })
    },
    /*Clears address find */
    'clearFinditSearch': function ()
    {
        mapmain.removeMapLayer(mapmain.config.findItShapeLayer, 0);
    },
    /*Attach travel time layer */
    'attachTravelTimeLayer': function (zoom) {
        mapmain.removeMapLayer(mapmain.config.TravelTimeLayer, 0);
        createTravelTimeLayer(mapmain.getZoom());
    },
    /*Attach traffic congestion layer. Using useBingTrafficCongestion property
     this layer can be switched between custom and Bing layers*/
    'attachRoadCongestionLayer': function () {

        var map = mapmain.config.map;

        if (mapmain.config.useBingTrafficCongestion) {

            
            Microsoft.Maps.loadModule('Microsoft.Maps.Traffic', {
                callback: function () {

                    setTimeout(function () {
                        Microsoft.Maps.loadModule('Microsoft.Maps.Traffic', { callback: trafficModuleLoaded });
                    }, 500);
                    
                }
            });

        }
        else {//SDOT custom layer
            mapmain.removeMapLayer(mapmain.config.roadSegmentLayer, mapmain.config.roadSegmentLayerTimeoutId);
            var roadSegmentLayer = createRoadSegmentTileLayer("DoIT.Travelers.RoadSegments", 1);
            mapmain.config.roadSegmentLayer = roadSegmentLayer;
            map.layers.insert(roadSegmentLayer);

            mapmain.config.roadSegmentLayerTimeoutId = setTimeout("mapmain.attachRoadCongestionLayer()", ROAD_SEGMENT_REFRESH_FREQ);
        }
         
    },
    'attachNeighborhoodLayer': function (zoom) {
        if (mapmain.config.neighborhoodsLayer != null) {
            mapmain.removeMapLayer(mapmain.config.neighborhoodsLayer, 0);
        }

        var opacity = 0.5;
        createNeighborhoodLayer(opacity, zoom);     
  
    },
    'attachNeighborhoodLabelList': function (zoom) {

        if (mapmain.config.neighborhoodLabelList == null && zoom <= 12) {
            createNeighborhoodLabelList();
        }

    },
    /*Attaches camera layer */
    'attachCameraLayer': function (zoom) {
        mapmain.removeMapLayer(mapmain.config.cameraLayer, 0);
        createCameraLayer(zoom);
    },
    /*Attaches events/incidents layer */
    'attachAlertsLayer': function (zoom) {
        createAlertsLayer(zoom);

        mapmain.clearAlertsLayerInterval();

        mapmain.config.alertsIntervalId = setInterval(function () {
                                            createAlertsLayer(zoom);
                                        }, REFRESH_ALERT);
    },
    /*Clears the auto-refresh for alerts*/
    'clearAlertsLayerInterval': function () {

        if (mapmain.config.alertsIntervalId > 0) {
            window.clearInterval(mapmain.config.alertsIntervalId);
        }
    },
    /*Attaches DMSSigns-On layer */
    'attachDMSSignsOnLayer': function () {

        createDMSSignsLayer("On");
        mapmain.clearDMSSignsOnLayerInterval();
        mapmain.config.dmsSignsOnIntervalId = setInterval(function () {
            createDMSSignsLayer("On");
        }, REFRESH_DMS_SIGN_ON);

    },
    /*Clears auto-refresh for DMSSigns-On layer */
    'clearDMSSignsOnLayerInterval': function () {

        if (mapmain.config.dmsSignsOnIntervalId > 0) {
            window.clearInterval(mapmain.config.dmsSignsOnIntervalId);
        }
    },
    /*Attach DMS SignsOff layer */
    'attachDMSSignsOffLayer': function () {

        createDMSSignsLayer("Off");
        mapmain.clearDMSSignsOffLayerInterval();
        mapmain.config.dmsSignsOffIntervalId = setInterval(function () {
            createDMSSignsLayer("Off");
        }, REFRESH_DMS_SIGN_OFF);

    },
    /*Clears auto-refresh for DMSSigns-Off layer */
    'clearDMSSignsOffLayerInterval': function () {

        if (mapmain.config.dmsSignsOffIntervalId > 0) {
            window.clearInterval(mapmain.config.dmsSignsOffIntervalId);
        }
    },
    /*Attach bridges-Open layer */
    'attachBridgesOpenLayer': function () {
        
        createBridgesLayer("Open");
        mapmain.clearBridgesOpenLayerInterval();
        mapmain.config.bridgesOpenIntervalId = setInterval(function () {
                                                createBridgesLayer("Open");
                                            }, REFRESH_BRIDGE_OPEN);

    },
    /*Clears auto-refresh for Bridges-Open layer*/
    'clearBridgesOpenLayerInterval': function () {

        if (mapmain.config.bridgesOpenIntervalId > 0) {
            window.clearInterval(mapmain.config.bridgesOpenIntervalId);
        }
    },
    /*Attach Bridges-Closed layer */
    'attachBridgesClosedLayer': function () {
        
        createBridgesLayer("Closed");
        mapmain.clearBridgesClosedLayerInterval();
        mapmain.config.bridgesClosedIntervalId = setInterval(function () {
                                                createBridgesLayer("Closed");
                                            }, REFRESH_BRIDGE_CLOSED);
    },
    /*Clears auto-refresh for Bridges-closed layer */
    'clearBridgesClosedLayerInterval': function () {

        if (mapmain.config.bridgesClosedIntervalId > 0) {
            window.clearInterval(mapmain.config.bridgesClosedIntervalId);
        }
    },
    /*Attach RR Xing-Open layer */
    'attachRRCrossingsOpenLayer': function () {
        createRRCrossingsLayer("Open");
        mapmain.clearRRCrossingsOpenLayerInterval();
        mapmain.config.rrCrossingsOpenIntervalId = setInterval(function () {
                                                    createRRCrossingsLayer("Open");
                                                }, REFRESH_RRCROSSING_OPEN);
    },
    /*Clears auto-refresh for RR Xings-Open layer */
    'clearRRCrossingsOpenLayerInterval': function () {

        if (mapmain.config.rrCrossingsOpenIntervalId > 0) {
            window.clearInterval(mapmain.config.rrCrossingsOpenIntervalId);
        }
    },
    /*Attach RR-xing-Closed layer */
    'attachRRCrossingsClosedLayer': function () {
        createRRCrossingsLayer("Closed");
        mapmain.clearRRCrossingsClosedLayerInterval();
        mapmain.config.rrCrossingsClosedIntervalId = setInterval(function () {
                                                createRRCrossingsLayer("Closed");
                                            }, REFRESH_RRCROSSING_CLOSED);
    },
    /*Clears auto-refresh for RR-Xings-Closed layer */
    'clearRRCrossingsClosedLayerInterval': function () {

        if (mapmain.config.rrCrossingsClosedIntervalId > 0) {
            window.clearInterval(mapmain.config.rrCrossingsClosedIntervalId);
        }
    },
    /*Removes a map layer */
    'removeMapLayer': function (mapLayer, timeoutId) {
        if (timeoutId != 0) clearTimeout(timeoutId);

        var map = mapmain.config.map;
        if (mapLayer != null) {
            
            //this is used to hide Bing Traffic layer
            if (typeof mapLayer.hideFlow === 'function') {
                mapLayer.hideFlow();
            }
            else if (typeof mapLayer.hide === 'function') {
                mapLayer.hide();
            }

            var doesExist = map.layers.indexOf(mapLayer) > -1;

            if (doesExist) map.layers.remove(mapLayer);
        }
        
    },
    /*Gets current zoom level */
    'getZoom': function () {
        var map = mapmain.config.map;
        return map.getZoom()
    },
    /*Gets current map center */
    'getCenter': function () {
        var map = mapmain.config.map;
        return map.getCenter();
    },
    /*Refresh camera and alerts layer as map view is changed */
    'onViewChangeEnd': function (e) {
        var map = mapmain.config.map;
        var zoom = map.getZoom();

        if (mapmain.config.lastZoomLevel != zoom) {
            
            if (mapmain.config.cameraLayerEnabled) { mapmain.attachCameraLayer(zoom); }
            if (mapmain.config.alertsLayerEnabled) { mapmain.attachAlertsLayer(zoom); }

            if (mapmain.config.neighborhoodsLayerEnabled && (mapmain.config.lastZoomRange != getCurrentZoomRange(zoom)))
            {
                mapmain.attachNeighborhoodLayer(zoom);
            }

            mapmain.config.lastZoomLevel = map.getZoom();
            mapmain.config.lastZoomRange = getCurrentZoomRange(zoom);

            //attach or detach neighborhood label layer
            if (mapmain.config.neighborhoodsLayerEnabled && mapmain.config.neighborhoodLabelList == null && zoom <= 12) {
                createNeighborhoodLabelList();
            }
            else if (zoom > 12 && mapmain.config.neighborhoodLabelList != null)
            {
                for (i = 0; i < mapmain.config.neighborhoodLabelList.length; ++i) {
                    mapmain.config.neighborhoodLabelList[i].setMap(null);
                }
                mapmain.config.neighborhoodLabelList = null;
            }
        }     
    },
 
};
/*Get zoom range from zoom level
    Range=1 when zoom level is <=12
    Range=2 when zoom level is >12
*/
function getCurrentZoomRange(zoom) {

    return (zoom <= 12) ? 1 : 2;
}
/*Create DMS layer with On/Off states */
function createDMSSignsLayer(state) {

    if (state == "On") {
        mapmain.removeMapLayer(mapmain.config.dmsSignsOnLayer, 0);
    }
    else if (state == "Off") {
        mapmain.removeMapLayer(mapmain.config.dmsSignsOffLayer, 0);
    }

    var signsShapeLayer = null;
    //get DMS data
    $.ajax({
        url: index.config.appRoot + "api/Map/GetDMSSignData",
        dataType: 'json',
        success: function (feed) {

            feed = $.parseJSON(feed);
            if (feed && feed.length > 0) {
                var feature = feed;
                var shape = null;
                var coordinate = null;

                signsShapeLayer = utils.createShapeLayer("DoIT.Travelers.DMS" + state, false);

                for (var i = 0; i < feature.length; ++i) {
                    var signItem = feature[i];

                    if (signItem != null) {

                        if (signItem.Status != state) continue;

                        coordinate = $.parseJSON('[' + signItem.Latitude + ',' + signItem.Longitude + ']');
                        shape = utils.createPushpin(coordinate,
                                            utils.getDMSSignFeatureIconUrl(signItem),
                                            38,
                                            33, true);

                        shape.Sign = signItem;
                        signsShapeLayer.add(shape);
                        Microsoft.Maps.Events.addHandler(shape, 'click', function (e) {
                            dms.showSignInfo(e.target.Sign, shape);
                        });
                    }
                }

                mapmain.config.map.layers.insert(signsShapeLayer);

                if (state == "On") {
                    mapmain.config.dmsSignsOnLayer = signsShapeLayer;
                }
                else if (state == "Off") {
                    mapmain.config.dmsSignsOffLayer = signsShapeLayer;
                }

            }

        }
    });
}

/*Create Bridge layer for Open/Closed status */
function createBridgesLayer(state) {

    if (state == "Open")
    {
        mapmain.removeMapLayer(mapmain.config.bridgesOpenLayer, 0);
    }
    else if (state == "Closed") {
        mapmain.removeMapLayer(mapmain.config.bridgesClosedLayer, 0);
    }

    var bridgesShapeLayer = null;

    //get bridge data
    $.ajax({
        url: index.config.appRoot + "api/Map/GetBridgeData",
        dataType: 'json',
        success: function (feed) {

            feed = $.parseJSON(feed);
            if (feed && feed.length > 0) {
                var feature = feed;
                var shape = null;
                var coordinate = null;

                bridgesShapeLayer = utils.createShapeLayer("DoIT.Travelers.Bridges" + state, false);

                for (var i = 0; i < feature.length; ++i) {
                    var bridgeItem = feature[i];

                    if (bridgeItem != null) {

                        if (bridgeItem.Status != state) continue;

                        coordinate = $.parseJSON('[' + bridgeItem.Latitude + ',' + bridgeItem.Longitude + ']');
                        shape = utils.createPushpin(coordinate,
                                            utils.getBridgeFeatureIconUrl(bridgeItem),
                                            36,
                                            33, true);

                        shape.Bridge = bridgeItem;
                        bridgesShapeLayer.add(shape);
                        //attach events
                        Microsoft.Maps.Events.addHandler(shape, 'click', function (e) {
                            bridge.showBridgeInfo(e.target.Bridge);
                        });
                    }
                }

                mapmain.config.map.layers.insert(bridgesShapeLayer);

                if (state == "Open") {
                    mapmain.config.bridgesOpenLayer = bridgesShapeLayer;
                }
                else if (state == "Closed") {
                    mapmain.config.bridgesClosedLayer = bridgesShapeLayer;
                }

            }

        }
    });
}

/*Create RR Xing layer for Open/Closed states */
function createRRCrossingsLayer(state) {

    if (state == "Open") {
        mapmain.removeMapLayer(mapmain.config.rrCrossingsOpenLayer, 0);
    }
    else if (state == "Closed") {
        mapmain.removeMapLayer(mapmain.config.rrCrossingsClosedLayer, 0);
    }

    var rrCrossingsShapeLayer = null;
    //get data
    $.ajax({
        url: index.config.appRoot + "api/Map/GetRRCrossingData",
        dataType: 'json',
        success: function (feed) {

            feed = $.parseJSON(feed);
            if (feed && feed.length > 0) {
                var feature = feed;
                var shape = null;
                var coordinate = null;

                rrCrossingsShapeLayer = utils.createShapeLayer("DoIT.Travelers.RRCrossings" + state, false);

                for (var i = 0; i < feature.length; ++i) {
                    var rrCrossingItem = feature[i];

                    if (rrCrossingItem != null) {

                        if (rrCrossingItem.Status != state) continue;

                        coordinate = $.parseJSON('[' + rrCrossingItem.Latitude + ',' + rrCrossingItem.Longitude + ']');
                        shape = utils.createPushpin(coordinate,
                                            utils.getRRCrossingFeatureIconUrl(rrCrossingItem),
                                            36,
                                            33, true);

                        shape.RRCrossing = rrCrossingItem;
                        rrCrossingsShapeLayer.add(shape);
                        //attach infobox events
                        Microsoft.Maps.Events.addHandler(shape, 'click', function (e) {
                            rrCrossing.showRRCrossingInfo(e.target.RRCrossing, shape);
                        });
                    }
                }

                mapmain.config.map.layers.insert(rrCrossingsShapeLayer);

                if (state == "Open") {
                    mapmain.config.rrCrossingsOpenLayer = rrCrossingsShapeLayer;
                }
                else if (state == "Closed") {
                    mapmain.config.rrCrossingsClosedLayer = rrCrossingsShapeLayer;
                }

            }

        }
    });
}
/*Create Alerts(Events and Incidents) layer based on the map zoom level */
function createAlertsLayer(zoomLevel) {

    mapmain.removeMapLayer(mapmain.config.alertsLayer, 0);


    var alertsShapeLayer = null;
    //get data
    $.ajax({
        url: index.config.appRoot + "api/Map/Data?zoomId=" + zoomLevel + "&type=1",
        dataType: 'json',
        success: function (feed) {

            if (feed && feed.Features.length > 0) {
                var features = null;
                var shape = null;               
                var coordinate = null;

                features = feed.Features;
                if (features.length > 0) {

                    alertsShapeLayer = utils.createShapeLayer("DoIT.Travelers.TrafficAlerts", false);

                    for (var i = 0; i < features.length; ++i) {
                        var alerts = features[i];
                        
                        if (alerts != null) {
                            var categoryType = utils.getEventIncidentFeatureCategoryType(alerts);
                            var iconUrl = (categoryType == 1) ? utils.getIncidentFeatureIconUrl(alerts.Incidents) : utils.getEventFeatureIconUrl(alerts.Events);
                            shape = utils.createPushpin(alerts.PointCoordinate,
                                                    iconUrl,
                                                    36,
                                                    33, true);

                            shape.Alerts = alerts;
                            alertsShapeLayer.add(shape);
                            //attach click event
                            Microsoft.Maps.Events.addHandler(shape, 'click', function (e) {
                                trafficAlerts.intializeAlertsSlider(e.target.Alerts);
                            });
                        }
                    }

                    mapmain.config.map.layers.insert(alertsShapeLayer);

                    mapmain.config.alertsLayer = alertsShapeLayer;
                }
                
            }
            
        }
    });
}

/*Create travel time sites layer based on zoom level */
function createTravelTimeLayer(zoomLevel) {

    var travelTimeShapeLayer = null;
    //get data
    $.ajax({
        url: index.config.appRoot + "api/Map/Data?zoomId=" + zoomLevel + "&type=5",
        dataType: 'json',
        success: function (feed) {

            feed = feed.traveltime[0].traveltime_site[0];

            if (feed && feed.Features && feed.Features.length > 0) {
                var shape = null;
                var features = feed.Features;
                var feature = null;

                travelTimeShapeLayer = utils.createShapeLayer("DoIT.Travelers.TravelTime", false);
                for (var i = 0; i < features.length; ++i) {
                    feature = features[i];
                    if (feature != null) {
                        shape = utils.createPushpin(feature.PointCoordinate,
                                                utils.getTravelTimeFeatureIconUrl(),
                                                36,
                                                33, true);
                        if (shape) {
                            shape.Site = feature.Site[0];

                            travelTimeShapeLayer.add(shape);
                            //create sites events
                            Microsoft.Maps.Events.addHandler(shape, 'click', function (e) {
                                travelTime.intializeTravelTimeSlider(e.target.Site);
                            });
                        }
                    }
                }
             
                mapmain.config.map.layers.insert(travelTimeShapeLayer);
            }
            mapmain.config.travelTimeLayer = travelTimeShapeLayer;
        },
        error: function (request, status, error) {
            //alert(request.responseText);
        }
    });
}

/*Creates camera layer by zoom level */
function createCameraLayer(zoomLevel) {

    var cameraShapeLayer = null;
    //gets camera feed
    $.ajax({
        url: index.config.appRoot + "api/Map/Data?zoomId=" + zoomLevel + "&type=2",
        dataType: 'json',
        success: function (feed) {        
            if (feed && feed.Features && feed.Features.length > 0) {
                var shape = null;
                var features = feed.Features;
                var feature = null;
                var coordinate = null;
                var cameras = null;
                var desc = null;
                cameraShapeLayer = utils.createShapeLayer("DoIT.Travelers.TrafficCameras", true);
                for (var i = 0; i < features.length; ++i) {
                    feature = features[i];
                    cameras = feature.Cameras;
                    if (cameras != null) {
                        shape = utils.createPushpin(feature.PointCoordinate,
                                                utils.getCameraFeatureIconUrl(cameras),
                                                36,
                                                33, true);
                        if (shape) {
                            shape.Cameras = cameras;

                            cameraShapeLayer.add(shape);
                            Microsoft.Maps.Events.addHandler(shape, 'click', function (e) {
                                cameraVideo.intializeCameraSlider(e.target.Cameras);                           
                            });
                        }
                    }
                }

                mapmain.config.map.layers.insert(cameraShapeLayer);
            }
            mapmain.config.cameraLayer = cameraShapeLayer;
        }
    });
}

/*Event for Bing traffic layer*/
function trafficModuleLoaded() {

    var map = mapmain.config.map;
    mapmain.config.roadSegmentLayer = new Microsoft.Maps.Traffic.TrafficManager(map);
    mapmain.config.roadSegmentLayer.showFlow();
    mapmain.config.roadSegmentLayer.hideLegend();
}

/*Hides traffic data */
function hideTrafficData() {
    mapmain.config.roadSegmentLayer.hide();
}

/*Create Roadsegment custom layer */
function createRoadSegmentTileLayer(name, opacity) {
    var mm = Microsoft.Maps;
    var now = new Date();
    var minutes = now.getMinutes();
    var hour = now.getHours();
    var year = now.getFullYear();
    var month = now.getMonth();
    var day = now.getDate();
    var cacheId = year + "" + month + "" + day + "" + hour + "" + minutes;

    // Create the tile layer source
    var tileSource = new mm.TileSource({
        uriConstructor: index.config.appRoot + 'Services/TileService.ashx?layer=' + name + '&quadkey={quadkey}&cacheId=' + cacheId
    });

    // Create the layer
    var layer = new mm.TileLayer({ mercator: tileSource, opacity: opacity });
    return layer;
}

/*Create Neighborhood custom layer */
function createNeighborhoodLayer(opacity, zoom) {
    var mm = Microsoft.Maps;
   
    // Create the tile layer source
    var tileSource = new mm.TileSource({
        uriConstructor: index.config.appRoot + 'Services/NeighborhoodTileService.ashx?zoomId=' + zoom + '&quadkey={quadkey}'
    });

    // Create the layer
    var neighborhoodsLayer = new mm.TileLayer({ mercator: tileSource, opacity: opacity});
  
    mapmain.config.neighborhoodsLayer = neighborhoodsLayer;
    var map = mapmain.config.map;
    map.layers.insert(neighborhoodsLayer);

}


/*Create Neighborhood Labels layer */
function createNeighborhoodLabelList() {

    var map = mapmain.config.map;
    var neighborhoodLabelList = [  utils.createNeighborhoodLabel("NORTH", 47.7101739, -122.3143826),
                                    utils.createNeighborhoodLabel("NORTHWEST", 47.7101739, -122.3723826),
                                    utils.createNeighborhoodLabel("NORTHEAST", 47.6701739, -122.3143826),
                                    utils.createNeighborhoodLabel("BALLARD", 47.6701739, -122.4023826),
                                    utils.createNeighborhoodLabel("LAKE UNION", 47.6510739, -122.3553826),
                                    utils.createNeighborhoodLabel("MAGNOLIA/QUEEN ANNE", 47.6301739, -122.4150826),
                                    utils.createNeighborhoodLabel("EAST", 47.6301739, -122.3143826),
                                    utils.createNeighborhoodLabel("CENTRAL", 47.6031739, -122.3143826),
                                    utils.createNeighborhoodLabel("DOWNTOWN", 47.6090739, -122.3570826),
                                    utils.createNeighborhoodLabel("SOUTHEAST", 47.5451739, -122.2853826),
                                    utils.createNeighborhoodLabel("GREATER DUWAMISH", 47.5581739, -122.3503826),
                                    utils.createNeighborhoodLabel("SOUTHWEST", 47.5701739, -122.4123826),
                                    utils.createNeighborhoodLabel("DELRIDGE", 47.5225739, -122.3703826)];

    for (i = 0; i < neighborhoodLabelList.length; ++i)
    {
        neighborhoodLabelList[i].setMap(map);
    }

    mapmain.config.neighborhoodLabelList = neighborhoodLabelList;

}

/*Geocode search address */
function geocodeRequestForSearch()
{ 
    var map = mapmain.config.map;
    var whereTo = $("#input-zoom-to").val();
    var searchManager = createSearchManager();
    var userData = { name: 'Maps Test User', id: 'XYZ' }; 
    var request = 
    { 
        where: whereTo,
        count: 5, 
        bounds: map.getBounds(), 
        callback: onGeocodeSuccess, 
        errorCallback: onGeocodeFailed, 
        userData: userData 
    }; 
    searchManager.geocode(request);
}

/*Creates search manager for address search */
function createSearchManager() {
    var map = mapmain.config.map;
    var searchManager;
    Microsoft.Maps.loadModule('Microsoft.Maps.Search', function () {
        searchManager = new Microsoft.Maps.Search.SearchManager(map);
    });

    return searchManager;
}

/*Callback for address search results */
function onGeocodeSuccess(result, userData) 
{ 
    if (result) {
        var map = mapmain.config.map;

        var topResult = result.results && result.results[0];

        if (topResult) {
            var isWithinValidBounds = utils.isAddressWithinValidBounds(topResult.location.latitude, topResult.location.longitude);

            if (isWithinValidBounds) {
                mapmain.clearFinditSearch();
                map.setView({ center: topResult.location, zoom: 14 });
                var loc = $.parseJSON('[' + topResult.location.latitude + ',' + topResult.location.longitude + ']');
                var pushpin = utils.createPushpin(loc, FINDIT_ICON_PATH, 24, 15, false);
                //pushpin.Text = topResult.name;
                mapmain.config.findItShapeLayer = utils.createShapeLayer("DoIT.Travelers.FindIt", true);
                mapmain.config.findItShapeLayer.add(pushpin);
                mapmain.config.map.layers.insert(mapmain.config.findItShapeLayer)
            }
            else
            {
                alert("Address not found.")
            }
        } 
    } 
} 
/*Callback for address search fail */
function onGeocodeFailed(result, userData) { 
    //displayAlert('Geocode failed'); 
}
