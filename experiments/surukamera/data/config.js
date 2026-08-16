// ***********************************************************************
// Project          : Travelers.UI
// Author           : Graim George
// Created          : 03-03-2015
//
// Last Modified By : Graim George
// Last Modified On : 12-12-2016
// ***********************************************************************
// <summary>Configuration values used in the project</summary>
// ***********************************************************************

// Traveler Map Root Directory
var TRAVELERS_APP_PATH = ""; // "/travelers/"; 

/*Generic Declarations */
var ADD_TO_FAVS_TEXT = "Add To Favorites";
var REM_FROM_FAVS_TEXT = "Remove From Favorites"

var PROFILE_ICON_PATH = "content/images/profile.png";

var ADD_TO_FAVS_ICON = "content/images/favorites-add-icon.png";
var REM_FROM_FAVS_ICON = "content/images/favorites-remove-icon.png"

var MAP_ZOOM_RANGE_MIN = 11;
var MAP_ZOOM_RANGE_MAX = 16;

/**
    * Incidents and Events Settings
    */

//Event
var EVENT_ICON_PATH = TRAVELERS_APP_PATH + "content/images/Event.png?09347";
// SDOT icon path for clustered events
var EVENT_CLUSTERED_ICON_PATH = TRAVELERS_APP_PATH + "content/images/EventPlus.png?09347";

//Incidents
// SDOT icon path for traffic incidents
var INCIDENT_ICON_PATH = TRAVELERS_APP_PATH + "content/images/Alert.png?09347";
// SDOT icon path for clustered incidents
var INCIDENT_CLUSTERED_ICON_PATH = TRAVELERS_APP_PATH + "content/images/AlertPlus.png?09347";


/**
Road Segment Settings
*/
var ROAD_SEGMENT_REFRESH_FREQ = 10000;


/**
* Traffic Camera Settings
*/
// SDOT icon path.
var SDOT_TRAFFIC_CAMERA_ICON_PATH = TRAVELERS_APP_PATH + "content/images/Camera.png";
// SDOT clustered icon path.
var SDOT_CLUSTERED_TRAFFIC_CAMERA_ICON_PATH = TRAVELERS_APP_PATH + "content/images/CameraPlus.png";
// Current traffic camera image root paths
var SDOT_TRAFFIC_CAMERA_CURRENT_IMAGE_PATH = "https://www.seattle.gov/trafficcams/images/";
var WSDOT_TRAFFIC_CAMERA_CURRENT_IMAGE_PATH = "https://images.wsdot.wa.gov/nw/";
var PORT_TRAFFIC_CAMERA_CURRENT_IMAGE_PATH = "https://images.wsdot.wa.gov/portofseattle/";

/**
* FindIt
*/
var FINDIT_ICON_PATH = TRAVELERS_APP_PATH + "content/images/Pushpin.png?09347";


/**
* Congestion
*/
var SDOT_CONGESTION_ICON_PATH = TRAVELERS_APP_PATH + "content/images/Traffic.png?09347";

/**
* Neighborhood
*/
var SDOT_NEIGHBORHOOD_ICON_PATH = TRAVELERS_APP_PATH + "content/images/Neighborhoods.png?09347";

/**
* Travel Time
*/
var TRAVEL_TIME_ICON_PATH = TRAVELERS_APP_PATH + "content/images/TravelTime.png?09342";
var TRAVEL_TIME_FLAG_ICON_PATH = TRAVELERS_APP_PATH + "content/images/Flag.png?09342";
var TRAVEL_TIME_START_ICON_PATH = TRAVELERS_APP_PATH + "content/images/Start.png?09342";

/**
* DMS
*/
var SDOT_DMS_SIGN_ON_ICON_PATH = TRAVELERS_APP_PATH + "content/images/DMSOn.png?09347";
var SDOT_DMS_SIGN_OFF_ICON_PATH = TRAVELERS_APP_PATH + "content/images/DMSOff.png?09348";


/**
* Bridge
*/
var SDOT_BRIDGE_OPENED_ICON_PATH = TRAVELERS_APP_PATH + "content/images/BridgeUp.png?09347";
var SDOT_BRIDGE_CLOSED_ICON_PATH = TRAVELERS_APP_PATH + "content/images/BridgeDown.png?09348";


/**
* RR Crossing
*/
var SDOT_CROSSING_OPENED_ICON_PATH = TRAVELERS_APP_PATH + "content/images/RROpen.png?09349";
var SDOT_CROSSING_CLOSED_ICON_PATH = TRAVELERS_APP_PATH + "content/images/RRClosed.png?09349";

/**
* SSO Login
*/

var SEATTLE_SSO_ROOT = "https://web6.seattle.gov/doit/sso/";
var SEATTLE_SSO_CREATE_USER = SEATTLE_SSO_ROOT + "CreateUser.aspx";
var SEATTLE_SSO_PASSWORD_RESET = SEATTLE_SSO_ROOT + "PasswordReset.aspx";

/*
AUTO REFRESH VALUES(in milli seconds)
*/
var REFRESH_ALERT = 30000;
//var REFRESH_DMS = 6000;
var REFRESH_BRIDGE_OPEN = 18000;
var REFRESH_BRIDGE_CLOSED = 20000;
var REFRESH_RRCROSSING_OPEN = 22000;
var REFRESH_RRCROSSING_CLOSED = 24000;
var REFRESH_DMS_SIGN_ON = 14000;
var REFRESH_DMS_SIGN_OFF = 16000;


/*
DEFAULT PROFILE
*/

var USER_DEFAULT_PROFILE = '{' +
    '"userProfile":{' +
        '"firstName":"",' +
        '"lastName":"",' +
        '"primaryEmail":"",' +
        '"additionalEmails":"",' +
        '"location":"seattle, wa",' +
        '"location":{"lng":-122.3321,"lat":47.6062},' +
        '"zoom":13,' +
        '"mapLegendOpened":false,' +
        '"mapLegend":{  ' +
            '"alerts":true,' +
            '"travelTimes":false,' +
            '"cameras":true,' +
            '"dmsSignsOn":false,' +
            '"dmsSignsOff":false,' +
            '"bridgesOpen":false,' +
            '"bridgesClosed":false,' +
            '"rrCrossingsOpen":false,' +
            '"rrCrossingsClosed":false,' +
            '"neighborhoods":false,' +
            '"congestion":true' + 
        '},' +
        '"favorites":{' +
            '"cameras":[  ],' +
            '"commutes":[ ],' +
            '"bridges":[ ],' +
            '"rrCrossings":[ ]' +
        '},' +
        '"notifications" : {' +
            '"eventIncidentNeighborhoods" : [],' +
            '"commuteAlert" : false,' +
            '"bridgeAlert" : false,' +
            '"rrCrossingAlert" : false,' +
            '"notifyAnyTime" : true,' +
            '"notifyStartDuration1" : 0,' +
            '"notifyEndDuration1" : 0,' +
            '"notifyStartDuration2" : 0,' +
            '"notifyEndDuration2" : 0' +
        '}' +
    '}' +
'}';
